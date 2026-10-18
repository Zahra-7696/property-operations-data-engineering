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

---

## Stage 5 — Event deduplication and current visit state

### Scope

Stage 5 starts from the technically valid physical deliveries produced by Stage 4
and separates two concerns:

1. **global event deduplication** by `event_id`; and
2. **visit-state precedence** by `visit_id` + `source_version`.

The batch arrival order is used for processing, but it does not determine which
visit version wins. A visit changes state only when an incoming
`source_version` is strictly greater than the currently stored version.

### `visit_events`

**Grain:** one globally unique `event_id`.

Important fields include:

- `visit_id`
- `source_version`
- `operation`
- parsed visit payload
- first source batch and line
- `state_action` = `applied` or `stale`
- `prior_source_version`

A stale unique event remains in history even though it does not change the
current visit state.

### `visit_event_duplicates`

**Grain:** one repeated physical delivery of an already-known global `event_id`.

The row records:

- the duplicate batch/line;
- the repeated `event_id`;
- the canonical first batch/line.

The duplicate is not counted as a second unique event.

### `visit_current_state`

**Grain:** one `visit_id`.

The row keeps the highest `source_version` observed for that visit.

A delete is retained as a tombstone (`is_deleted = TRUE`). This prevents an
older upsert from resurrecting the visit. A genuinely newer upsert may reactivate
the visit because its version is higher.

### Expected Stage 5 counts

```text
1,979 technically valid physical deliveries
1,944 globally unique event IDs
35 duplicate deliveries

1,890 applied unique state changes
54 stale/equal-version unique events
  49 lower-version stale events
   5 equal-version stale events

1,618 distinct current visit states
1,567 active current visit states
51 deleted current visit states
```

### Key source-behaviour examples

`V02` demonstrates the tombstone rule:

```text
v1 upsert
v2 delete
later v1 upsert
```

The final state remains deleted at v2.

`V04` demonstrates legitimate reactivation:

```text
v1 upsert
v2 delete
v3 upsert
later v1 upsert
```

The v3 upsert becomes current because it is newer than the delete.

### Audit evidence

The same cumulative:

```text
reports/Report.txt
```

is extended with:

- Stage 5 per-batch counts;
- unique/duplicate/applied/stale totals;
- all duplicate delivery references;
- all stale/equal-version event references and reasons;
- key V02/V03/V04 current-state examples;
- all deleted current-state tombstones.

The report is regenerated from database state rather than appended as an
unstructured log.

---

## Stage 6 — Business quality on current active visits

### Scope

Stage 6 evaluates the **latest active visit state** produced by Stage 5.

These records are already technically valid source events. Stage 6 asks whether
their business references and duration are suitable for reporting.

The assignment defines three business-quality failures:

- unknown property reference;
- unknown contractor reference;
- completed visit where `ended_at < started_at`.

Deleted visits are not quality failures. An unfinished visit with
`ended_at = NULL` is valid.

### `visit_quality_issues`

**Grain:** one business-quality issue per `visit_id` + `issue_code`.

Primary key:

```text
(visit_id, issue_code)
```

The table preserves:

- affected visit;
- issue code and explanation;
- winning/current event ID;
- source batch and line;
- property/contractor values;
- visit start/end timestamps.

For `end_before_start`, the console and cumulative audit report show the exact
UTC start/end timestamps and elapsed minutes. For `V11`, the evidence is:

```text
started_at = 2026-03-25T01:00:00Z
ended_at   = 2026-03-25T00:00:00Z
duration   = -60.0 minutes
```

### `reportable_current_visits`

**Grain:** one latest active visit that passes all Stage 6 business-quality checks.

This is a view over `visit_current_state`:

```text
active current visits
MINUS
visits present in visit_quality_issues
```

Deleted visits and business-invalid active visits therefore do not enter visit
metrics, while unfinished valid visits remain included.

### Expected Stage 6 results

```text
1,618 current visit states
   51 deleted tombstones
1,567 active current visits
    3 active visits with business-quality issues
1,564 reportable active visits
```

The three affected visits are:

```text
V11 -> end_before_start
V12 -> unknown_property
V13 -> unknown_contractor
```

### Audit evidence

The cumulative `reports/Report.txt` is extended with:

- active/deleted/reportable counts;
- issue counts by category;
- each affected visit;
- winning event and exact source reference;
- the reason the visit is excluded from metrics.

The source/current-state record is preserved. Stage 6 excludes bad business
records from reporting; it does not erase them.

---

## Stage 7 — Property-management history

### Scope

Stage 7 transforms unsorted management observations into contiguous historical
intervals for each property.

`effective_at` is business time. Consecutive observations that name the same
company do not create new business periods and are collapsed into one interval.
If a property later returns to a company that managed it previously, that return
is a new interval because another company managed the property in between.

Intervals use:

```text
[valid_from, valid_to)
```

so `valid_from` is included and `valid_to` is excluded.

### `property_management_intervals`

**Grain:** one contiguous company-management interval for one property.

Primary key:

```text
(property_id, valid_from)
```

Important fields:

- `property_id`
- `company_id`
- `valid_from`
- `valid_to`
- `start_observation_id`
- `source_observation_count`

The `source_observation_count` makes unchanged-observation collapsing visible
instead of silently discarding those source statements.

### `current_property_management`

**Grain:** one current management row per property.

This view selects the open interval (`valid_to IS NULL`) and adds property/company
names. It exists specifically so current management is visibly different from
historical management at an earlier visit or incident time.

### Expected Stage 7 results

```text
62 source management observations
37 historical management intervals
25 unchanged observations collapsed
24 properties
24 current management rows
```

### Important examples

`P01` proves that a return to a previous manager is a new interval:

```text
C01 -> C02 -> C01
```

The two `C01` periods are not merged because `C02` managed the property between
them.

`P03` changes from `C02` to `C03` at:

```text
2026-03-29T13:00:00Z
```

With `[valid_from, valid_to)` semantics:

```text
12:59 -> C02
13:00 -> C03
```

The supplied boundary records therefore attribute:

```text
V09 / I07 at 12:59 -> C02
V10 / I06 at 13:00 -> C03
```

### Audit evidence

The cumulative `reports/Report.txt` adds:

- all 37 reconstructed intervals;
- observation/interval/current counts;
- P01 return-to-previous-manager evidence;
- P03 transfer-boundary evidence;
- the 24-row current-management lookup.

Stage 8 uses this historical model to attribute reportable visits by
`started_at` and incidents by `reported_at`.

---

## Stage 8 — Analytical visit and incident facts

### Scope

Stage 8 converts the clean current visit population and supplied incidents into
separate analytical fact tables.

The two facts remain separate because there is no incident-to-visit or
incident-to-contractor key. Raw joining them would multiply rows and inflate
metrics.

Historical company attribution uses:

```text
visit    -> started_at
incident -> reported_at
```

Reporting week is the Monday date derived after converting the corresponding
business timestamp into the property's IANA timezone.

### Deterministic date/time session

All DuckDB connections set:

```text
TimeZone = UTC
Calendar = gregorian
```

The explicit Gregorian calendar is important because reporting-week labels are
Gregorian `DATE` values. Property-local timestamps are then derived explicitly
from the stored `TIMESTAMPTZ` instant using each property's IANA timezone.

### `fact_visits`

**Grain:** one reportable current business visit.

Primary key:

```text
visit_id
```

Important fields include:

- property and contractor identifiers/names;
- visitor ID;
- historical managing company at `started_at`;
- UTC start/end;
- property-local start/end;
- local Monday `week_start_local`;
- `is_requested_week`;
- `is_unfinished`;
- elapsed `completed_hours`;
- current source version and winning event.

Unfinished visits remain facts and contribute no completed hours.

### `fact_incidents`

**Grain:** one supplied incident.

Primary key:

```text
incident_id
```

Important fields include:

- property;
- historical managing company at `reported_at`;
- UTC/local reported timestamp;
- local Monday `week_start_local`;
- `is_requested_week`;
- severity and `is_high`.

### Requested-week boundary

`src_report_weeks` contains the six requested local Monday labels. Facts outside
those labels are retained and marked `is_requested_week = FALSE`; they are not
quality failures.

Expected Stage 8 counts:

```text
1,564 visit facts in total
1,543 visits in requested local weeks
21 visits outside requested weeks

247 incident facts in total
246 incidents in requested local weeks
1 incident outside requested weeks
```

### Property-local week example

For `P01` (`Pacific/Auckland`):

```text
V14  2026-03-22T10:59:00Z -> local 2026-03-22 23:59 -> week 2026-03-16
V01  2026-03-22T11:00:00Z -> local 2026-03-23 00:00 -> week 2026-03-23
```

One UTC minute crosses the property's local Monday boundary.

### DST and completed hours

Completed hours are elapsed time between UTC instants, not subtraction of local
wall-clock display values.

`V07` and `V16` cross the New Zealand daylight-saving transition:

```text
local start  2026-04-05 02:30
local end    2026-04-05 03:30
elapsed UTC  2.00 hours
```

The displayed local clocks differ by one hour because the UTC offset changes,
but the actual elapsed visit duration is two hours.

### Requested-week metric checks

The Stage 8 facts produce:

```text
visit count              1,543
distinct visitors          259
completed visit hours   2,053.50
unfinished visits           91
incident count             246
high-severity incidents     31
```

These are sanity checks for the fact layer. Stage 9 builds the explicit reporting
marts at company/week, property/week and contractor grains.

