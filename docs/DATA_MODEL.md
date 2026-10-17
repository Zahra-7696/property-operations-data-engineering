# Data model notes

## Stage 2 scope

Stage 2 creates a persistent DuckDB database containing typed copies of the
static/reference source files. The supplied files under `data/` remain the
immutable source of truth.

The visit-change JSONL batches are intentionally **not** loaded in this stage.
They require dedicated validation, duplicate handling, version precedence,
delete/reactivation logic and batch audit controls, which are implemented in the
next stages.

## Typed source tables

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

## Logical relationships

- `src_property_management.property_id` -> `src_properties.property_id`
- `src_property_management.company_id` -> `src_companies.company_id`
- `src_incidents.property_id` -> `src_properties.property_id`

These logical relationships are validated explicitly during loading and in
automated tests.

## Why the database tables use the `src_` prefix

The raw files are preserved byte-for-byte in the repository. The `src_` tables
are typed database copies of those sources, not final analytical models.

Later stages will build separate history, fact and reporting models instead of
mixing source ingestion with business analytics.

## Atomic reference loading

The reference-data loader runs inside a transaction:

1. start transaction;
2. clear the existing typed reference tables;
3. reload all six reference sources;
4. validate row counts and logical references;
5. commit only if every check passes.

If an error occurs before commit, the transaction is rolled back. This gives the
static/reference layer a simple reproducible rebuild while the more complex
incremental visit-batch semantics are implemented separately.
