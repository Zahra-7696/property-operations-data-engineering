# Data model notes

## Project layering

The project keeps different responsibilities separate:

```text
Supplied files (immutable)
        |
        v
Typed static/reference source tables            [Stage 2]
        |
        v
Raw visit-batch landing                         [Stage 3]
        |
        v
Technical validation / event history            [Stage 4]
        |
        v
Current visit state / quality models            [Stages 5-6]
        |
        v
Management history                              [Stage 7]
        |
        v
Analytical facts                                [Stage 8]
        |
        v
Gold reporting marts                            [Stage 9]
        |
        v
HTML operations report                          [Stage 10]
```

The data model is cumulative. Each stage extends this file; earlier stage
definitions remain so the complete evolution of the model is visible in one place.

---

## Stage 2 — Typed static/reference source layer

### Scope

Stage 2 creates a persistent DuckDB database containing typed copies of the
static/reference source files. The supplied files under `data/` remain the
immutable source of truth.

The visit-change JSONL batches are intentionally **not** loaded in this stage.
They require dedicated validation, duplicate handling, version precedence,
delete/reactivation logic and batch audit controls, which are implemented in
later stages.

### Typed source tables

| Table | Grain | Primary key | Important fields |
| --- | --- | --- | --- |
| `src_companies` | One managing company | `company_id` | `company_name` |
| `src_contractors` | One contractor | `contractor_id` | `contractor_name` |
| `src_properties` | One property | `property_id` | `property_name`, `timezone` |
| `src_property_management` | One management observation | `observation_id` | `property_id`, `company_id`, `effective_at` |
| `src_incidents` | One incident | `incident_id` | `property_id`, `reported_at`, `severity` |
| `src_report_weeks` | One requested reporting week | `week_start` | local Monday date |

Timestamps are stored as `TIMESTAMPTZ` and requested report week labels are
stored as `DATE`.

### Logical relationships

- `src_property_management.property_id` -> `src_properties.property_id`
- `src_property_management.company_id` -> `src_companies.company_id`
- `src_incidents.property_id` -> `src_properties.property_id`

These logical relationships are validated explicitly during loading and in
automated tests.

### Why the database tables use the `src_` prefix

The raw files are preserved byte-for-byte in the repository. The `src_` tables
are typed database copies of those sources, not final analytical models.

Later stages build separate history, fact and reporting models instead of mixing
source ingestion with business analytics.

### Atomic reference loading

The reference-data loader runs inside one transaction:

1. start transaction;
2. clear the existing typed reference tables;
3. reload all six reference sources;
4. validate row counts and logical references;
5. commit only if every check passes.

If an error occurs before commit, the transaction is rolled back. This gives the
static/reference layer a simple reproducible rebuild while the more complex
incremental visit-batch semantics are implemented separately.

---

## Stage 3 — Raw visit-batch landing layer

### Scope

Stage 3 lands the six visit-change files in manifest order but does **not parse
JSON yet**.

Every physical JSONL line is preserved, including deliberately malformed lines.
This gives the technical-validation stage complete evidence and accurate
`batch_id` / `line_number` locations.

### `ingestion_batches`

**Grain:** one stable manifest batch ID.

Important columns:

- `batch_id`: stable batch identity from `manifest.json`
- `manifest_order`: initial processing order
- `source_file`: repository-relative source path
- `snapshot_date`: export snapshot label, not visit business time
- `source_sha256`: whole-file fingerprint used to guard stable batch contents
- `status`: `processing`, `landed`, `completed`, or `failed`
- `first_started_at`, `last_attempt_at`, `landed_at`
- `attempt_count`
- `input_lines`
- reserved processing counters for:
  - technically valid lines
  - technically invalid lines
  - unique event lines
  - duplicate event lines
  - applied state changes
  - stale event lines
  - business-quality issues
- `error_message`

At Stage 3, successful batches finish as `landed`. Later processing stages
advance them to `completed` only after technical validation and business-state
application are successful.

### `raw_visit_lines`

**Grain:** one physical line from one visit-change batch.

Primary key:

```text
(batch_id, line_number)
```

Important columns:

- `raw_payload`: source line as UTF-8 text without its line terminator
- `line_sha256`: fingerprint of the exact source line bytes, including its
  original line ending

Malformed JSON is intentionally retained here rather than discarded.

### Replay behaviour

A batch already in `landed` or `completed` status is a strict no-op if its
manifest metadata and source-file SHA256 are unchanged.

If an existing batch ID points to changed metadata or changed contents, loading
fails rather than silently treating changed data as the same batch.

### Restart behaviour

A batch landing attempt runs its raw-row writes in a transaction.

On failure:

1. the raw-row transaction is rolled back;
2. the batch is recorded as `failed`;
3. no partial raw lines remain for that failed batch;
4. rerunning retries that batch;
5. already landed earlier batches remain untouched.

This establishes restart safety at the raw landing layer. Later stages extend the
same principle to parsed events and business state.

---

## Current model boundary

After Stage 3 the database contains:

```text
Static/reference typed source tables
    src_companies
    src_contractors
    src_properties
    src_property_management
    src_incidents
    src_report_weeks

Raw visit landing / audit tables
    ingestion_batches
    raw_visit_lines
```

It does **not yet** contain:

```text
parsed visit events
technical reject records
deduplicated event history
current visit state
business-quality issue models
management intervals
analytical facts
gold reporting marts
```

Those are added incrementally in the following stages so each responsibility
remains testable and explainable.

---

## Stage 4 — Technical visit-event validation

### Scope

Stage 4 parses the raw JSONL lines landed in Stage 3 and separates technically
valid source events from technically invalid lines.

This stage follows the field-level technical rules in the supplied data
dictionary. It deliberately does **not** perform business-quality checks such as
unknown property/contractor references or `ended_at < started_at`; those remain a
separate later stage.

### `validated_visit_events`

**Grain:** one technically valid physical event-delivery line.

Primary key:

```text
(batch_id, line_number)
```

Important columns:

- `event_id`
- `visit_id`
- `source_version`
- `operation`
- `updated_at`
- complete upsert payload fields where applicable
- `raw_payload`

Repeated `event_id` values are intentionally allowed here. Deduplication and
per-visit version precedence are Stage 5 concerns.

### `visit_event_rejects`

**Grain:** one technically invalid physical input line.

Primary key:

```text
(batch_id, line_number)
```

Important columns:

- original `raw_payload`
- `reason_codes`
- human-readable `rejection_reason`
- `rejected_at`

### Technical validation rules

The validator checks:

- JSON parses to an object.
- `event_id` is a nonempty string.
- `visit_id` is a nonempty string.
- `source_version` is a positive integer and not a boolean.
- `operation` is `upsert` or `delete`.
- `updated_at` is timezone-aware ISO 8601.
- upserts contain nonempty `property_id`, `contractor_id`, `visitor_id`.
- upserts contain timezone-aware `started_at`.
- `ended_at` exists for upserts and is null or timezone-aware.
- deletes omit visit payload fields.

Unknown references and invalid business duration are not technical rejects.

### Expected Stage 4 counts

```text
1,997 raw input lines
1,979 technically valid event-delivery lines
18 technically invalid lines
```

The 18 rejects comprise:

```text
6 malformed JSON lines
6 incomplete upserts
6 delete events with invalid updated_at timestamps
```



### Cumulative engineering/audit report

Stage 4 also introduces a generated text report:

```text
reports/Report.txt
```

This file is **not another data-model layer**. It is a human-readable engineering
audit artifact generated from the source manifests and DuckDB state.

At Stage 4 it contains cumulative evidence for:

- Stage 1 source integrity;
- Stage 2 typed reference counts;
- Stage 3 batch landing and raw-line counts;
- Stage 4 valid/invalid counts;
- Stage 4 rejection categories;
- every rejected source record with source file, line number, reason and raw text.

The report uses fixed-width ASCII tables for compact console/text readability.
Later stages extend this same report with duplicate, versioning, business-quality
and modelling evidence rather than creating separate stage-specific reports.

### Replay behaviour

Once a batch has `validated_at`, rerunning technical validation is a strict
no-op for that batch.

### Current model boundary after Stage 4

The database now contains:

```text
Static/reference typed source tables
Raw visit landing / audit tables
Technically validated visit layer
    validated_visit_events
    visit_event_rejects
```

It does not yet contain deduplicated event history, current visit state,
business-quality issues, management intervals, analytical facts or gold marts.

