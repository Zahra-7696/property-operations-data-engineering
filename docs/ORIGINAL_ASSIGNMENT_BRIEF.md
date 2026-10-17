# Take-home project — Property operations data and reporting

## Start here

No knowledge of Onside or its product is required. This is a self-contained,
fictional scenario, and the rules in this pack define how its data should work.
Start with [the business and relationship guide](BUSINESS_CONTEXT.md), then read
this brief and [the field-level data dictionary](DATA_DICTIONARY.md).

## The business problem

Meadow Operations is a fictional company supporting rural properties in New Zealand
and Australia. Its operations manager wants a reliable weekly view of contractor
visits and incidents, grouped by property and managing company.

The source exports are imperfect: visits arrive in batches and can be corrected
or deleted; some records are repeated or invalid; properties change managing
company. The current spreadsheets make it difficult to trust historical totals.

Build a small, reproducible data project that ingests these exports, models them
and produces a useful operations report. Work on it at home, commit the supplied
data and your project to a Git repository, and share it before the interview.
We will discuss the repository, your decisions and your findings together.

There is no elapsed-time limit. Agree the submission date with your interviewer.
Choose your tools, model names and report format. A local database, SQL/Python
and a viewable report are sufficient. **SQLite or DuckDB are suggested local
database options; either is suitable for this project.** Other tools are welcome.
Cloud deployment and a custom app are optional.

## The supplied data

All files are fictional and intended to be committed to your repository.

- Six ordered JSONL visit-change batches containing **1,997 input lines**.
- **24 properties**, **three managing companies** and **eight contractors**.
- **62 property-management observations** and **247 incidents**.
- Six requested reporting weeks, starting 2 March–6 April 2026.

`manifest.json` identifies batch order and snapshot dates. `DATASET_MANIFEST.json`
lists row counts and checksums. See [the data dictionary](DATA_DICTIONARY.md) for
fields, source grain and validation rules. Preserve the supplied raw files unchanged.

## What to build

**Ingestion and quality.** Load the source data into a persistent local store.
Maintain the latest known state of each visit, handle updates/deletes and make
rerunning a batch safe. Record batch progress and useful loading counts. Retain
evidence of invalid records. Demonstrate that a failed batch can be restarted
without missing or duplicating data.

**Data modelling.** Design clean source models, property-management history and
analytics models. Attribute visits and incidents to the managing company when
they occurred. Explain grains, keys, relationships and your handling of incomplete
or invalid data. Include a current-management lookup so the difference between
current and historical management is visible.

**Reporting.** Produce an operations report covering the requested local weeks.
Show visit count, distinct visitors, completed visit hours, unfinished visits,
incident count and high-severity incidents. Include:

- A weekly activity trend by managing company.
- A property summary, including properties and weeks with no activity.
- A contractor breakdown or drill-down for visit activity.
- Data-quality and load information that helps the reader judge the results.

Choose charts and tables that make the report useful. Include reporting period,
definitions and the latest successfully processed batch. Commit a viewable artifact
or screenshots and useful modelled extracts, with instructions to regenerate them.
An HTML report, notebook, spreadsheet or BI dashboard is fine.

**Validation and explanation.** Add automated checks for realistic ingestion,
modelling and reporting mistakes. Document how to rebuild from a fresh checkout.
Include a few findings, what an operations manager could investigate, and what
the data cannot establish. We care about the reasoning as well as the result.

## Business definitions

1. Initial processing follows manifest order. Batch IDs and contents are stable.
   Replaying a completed batch must not change state or historical load records.
2. `event_id` is globally unique; repeated IDs have identical payloads. Apply visit
   changes using the highest `source_version` **per visit**, not arrival time or a
   global timestamp watermark. Different event IDs may repeat an older state.
3. Upserts contain the full visit payload. A delete excludes the visit from active
   reporting. Retain enough version/deletion evidence to prevent an older upsert
   resurrecting it; a genuinely newer upsert may reactivate it.
4. Technically invalid events should be recorded with batch ID, line number and
   an explanation. Valid records should still load. A failed batch must not leave
   partially applied business state or a completed checkpoint.
5. Unknown property/contractor references and ends preceding starts are business
   quality issues. Keep evidence of them and exclude those visits from metrics.
   They can still be well-formed source events. Deleted visits are not quality failures.
6. Management `effective_at` is business time. Consecutive unchanged observations
   form one interval. A return to a previous manager creates a new interval.
   Intervals include their start and exclude their end. A visit or incident exactly
   at a transfer timestamp belongs to the new company.
7. Assign visits using `started_at` and incidents using `reported_at`. Each event
   belongs to the week starting Monday 00:00 in its property's IANA timezone,
   ending at the next Monday 00:00, excluding the final boundary. The data spans
   daylight-saving changes. Company charts use these property-local week labels.
8. An unfinished visit is valid and counts as a visit and distinct visitor, but
   contributes no completed hours. Completed hours are elapsed UTC time for the
   entire visit, assigned to its start week. Round totals after summing.
9. Distinct visitors are distinct within the displayed group; company totals
   cannot be calculated by adding property-level distinct counts. Each incident
   counts once, and high severity means exactly `severity = 'high'`. Combining
   visits and incidents must preserve their separate fact grains.
10. Reports use the latest successfully loaded visit state, including corrections
    received after a visit occurred. They are not a reconstruction of what a
    previous report knew at publication time. Valid records outside the requested
    weeks are outside this report, rather than quality failures.

## Your repository and the interview

See [submission guidance](PROJECT_GUIDANCE.md). Share the repository URL and access
with your interviewer; a private repo is welcome. Commit raw data, implementation,
dependency definitions, tests, model extracts, report and README. Exclude environments,
credentials and large generated database files.

In the interview, walk us through the data flow and report, trace a few records to
their metrics, and explain your design and tradeoffs. There are no prescribed
functions, output column layouts or supplied answer checkers.

Optional extensions include scheduling, incremental report refresh, a correction
audit trail or a discussion of scaling. Completing the core project well is sufficient.
