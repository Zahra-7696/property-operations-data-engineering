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
