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

---

---

---

## Candidate implementation

### Project approach

The project is implemented as a small reproducible data pipeline rather than a
set of unrelated scripts.

The planned data flow is:

```text
Supplied raw files
        |
        v
Source integrity verification
        |
        v
Typed DuckDB source tables
        |
        v
Visit batch ingestion + technical validation
        |
        v
Duplicate / version / delete handling
        |
        v
Business-quality checks
        |
        v
Clean current state + management history
        |
        v
Visit and incident analytical facts
        |
        v
Gold reporting models
        |
        v
Self-contained HTML operations report
```

The raw files under `data/` remain unchanged. Generated database files are
rebuildable and are not committed to Git.

### Technology choices

| Area | Tool | Purpose |
| --- | --- | --- |
| Language | Python | Ingestion, validation, orchestration and report generation |
| Local database | DuckDB | Persistent typed source, analytical and reporting models |
| Transformation | SQL | Clear, reviewable data modelling and aggregations |
| Testing | pytest | Automated ingestion, modelling and reporting checks |
| Report | Plotly + Jinja2 | Self-contained reviewable HTML report |
| Code quality | Ruff | Lightweight Python linting |
| CI | GitHub Actions | Automated validation on push / pull request |

The core implementation is intentionally local and reproducible. A production
AWS/Snowflake evolution will be documented only after the core assignment is
working correctly.

---

## Implementation roadmap

| Stage | Aim | Main output | Status |
| --- | --- | --- | --- |
| 1. Project setup and raw integrity | Preserve the supplied source exactly and establish a reproducible Python/Git project | `src/verify_sources.py`, dependencies, Git configuration | **Done** |
| 2. Persistent source layer | Create DuckDB and load/validate the six static/reference CSV sources | `sql/schema.sql`, `src/database.py`, `src/load_reference_data.py` | **Done** |
| 3. Visit batch ingestion | Read `manifest.json`, land all physical JSONL lines in order and record replay/restart-safe batch progress | `ingestion_batches`, `raw_visit_lines`, landing code/tests | **Done** |
| 4. Technical validation | Parse landed lines, retain valid source events and record technical rejects with evidence | `validated_visit_events`, `visit_event_rejects` | **Done** |
| 5. Versioning, duplicates and deletes | Deduplicate global `event_id`, apply highest `source_version` per visit and retain delete tombstones/reactivation | `visit_events`, `visit_event_duplicates`, `visit_current_state` | **Done** |
| 6. Business data quality | Flag unknown property/contractor references and invalid completed durations while preserving evidence | `visit_quality_issues`, `reportable_current_visits` | **Done** |
| 7. Management history | Convert observations into contiguous historical intervals and expose current management separately | `property_management_intervals`, `current_property_management` | **Done** |
| 8. Analytical facts | Attribute visits/incidents historically, derive property-local weeks and UTC elapsed hours | `fact_visits`, `fact_incidents` | **Done** |
| 9. Gold reporting models | Create company-week, property-week and contractor-ready reporting models | BI/reporting tables | Not started |
| 10. Operations report | Build a polished report with KPIs, trends, summaries, findings and quality/load evidence | `reports/operations_report.html` | Not started |
| 11. Full validation suite | Cover edge cases including replay, rollback, stale versions, transfer boundaries and DST | Automated test suite | Not started |
| 12. CI and reproducibility | Run checks automatically from a clean Git checkout | GitHub Actions workflow | Not started |
| 13. Production design | Explain how the solution could scale using AWS/S3/event-driven services/Snowflake | Architecture document | Not started |
| 14. Interview walkthrough | Trace sample records from source to final metrics and explain trade-offs | Interview-ready walkthrough | Not started |

---

## Current implementation status

### Completed

- [x] Preserve all supplied raw files unchanged
- [x] Verify raw source row counts against `DATASET_MANIFEST.json`
- [x] Verify raw source SHA256 checksums
- [x] Add `.gitattributes` to protect raw CSV/JSONL bytes from Git line-ending conversion
- [x] Create the persistent DuckDB schema
- [x] Load all six static/reference sources into typed tables
- [x] Validate reference-table row counts
- [x] Validate reference relationships and incident severity domain
- [x] Make reference-data reload transactional / rollback-safe

### Completed in Stage 2

- [x] Confirm Stage 2 automated pytest checks pass from the project root (`11 passed`)

### Completed in Stage 3

- [x] Land all 6 manifest batches in order
- [x] Preserve all 1,997 physical visit-change lines
- [x] Confirm replay is a true no-op
- [x] Confirm simulated batch failure rolls back and restarts safely
- [x] Confirm full regression suite passes (`17 passed`)

### Completed in Stage 4

- [x] Parse all 1,997 landed visit-change lines
- [x] Retain 1,979 technically valid event-delivery lines
- [x] Preserve 18 technical rejects with batch, line, category, reason and raw evidence
- [x] Generate cumulative `reports/Report.txt`
- [x] Present Stage 4 results in readable fixed-width tables
- [x] Confirm validation replay is a no-op
- [x] Confirm Stage 4 tests pass (`12 passed`)
- [x] Confirm full regression suite passes (`29 passed`)

### Completed in Stage 5

- [x] Deduplicate 1,979 technically valid physical deliveries into 1,944 unique event IDs
- [x] Preserve 35 duplicate-delivery references
- [x] Apply highest `source_version` per `visit_id`
- [x] Preserve stale/equal-version event history
- [x] Maintain 1,618 current visit states
- [x] Preserve 51 delete tombstones and allow newer reactivation
- [x] Confirm Stage 5 tests pass (`11 passed`)
- [x] Confirm full regression suite passes (`40 passed`)

### Completed in Stage 6

- [x] Flag unknown property references
- [x] Flag unknown contractor references
- [x] Flag completed visits where `ended_at < started_at`
- [x] Preserve quality evidence and exact source references
- [x] Show `V11` start/end timestamps and negative-duration proof
- [x] Exclude business-invalid active visits from `reportable_current_visits`
- [x] Keep deleted visits separate from quality failures
- [x] Keep unfinished visits valid
- [x] Confirm Stage 6 tests pass (`10 passed`)
- [x] Confirm full regression suite passes (`50 passed`)

### Completed in Stage 7

- [x] Collapse consecutive unchanged management observations
- [x] Build start-inclusive/end-exclusive management intervals
- [x] Preserve a return to a previous company as a new interval
- [x] Build one-row-per-property current-management lookup
- [x] Prove the exact P03 transfer boundary
- [x] Show all reconstructed management intervals on screen and in `reports/Report.txt`
- [x] Confirm Stage 7 tests pass (`10 passed`)
- [x] Confirm full regression suite passes (`60 passed`)

### Completed in Stage 8

- [x] Build one-row-per-visit analytical fact
- [x] Build one-row-per-incident analytical fact
- [x] Attribute each record to the historical managing company
- [x] Derive Monday reporting week in each property's IANA timezone
- [x] Calculate completed visit hours using elapsed UTC time
- [x] Preserve valid records outside the requested reporting weeks
- [x] Keep visits and incidents as separate fact grains
- [x] Add a one-command pipeline runner through Stage 8
- [x] Confirm Stage 8 tests pass (`11 passed`)
- [x] Confirm full regression suite passes (`71 passed`)

### Current stage

- [ ] Build 18 company/week trend rows
- [ ] Build company requested-period totals with direct distinct counts
- [ ] Build all 144 property/week rows, including zero-activity weeks
- [ ] Build the 8-row contractor breakdown
- [ ] Aggregate visits and incidents separately before combining metrics
- [ ] Prove distinct visitors are non-additive
- [ ] Extend the one-command pipeline through Stage 9
- [ ] Extend `reports/Report.txt` with reporting-mart evidence

### Next

- [ ] Generate the final business-facing HTML operations report and model extracts

---

---

## Stage 1 — Setup and raw-data integrity

### Aim

Establish a trustworthy and reproducible starting point before any modelling.
Stage 1 proves that every supplied raw file is the exact input delivered for the
assignment, protects those bytes from accidental Git/Windows line-ending changes,
and creates a Python/Git environment from which all later database results can be
rebuilt.

### Questions answered

- Are the raw files exactly the files supplied for the assignment?
- Have row counts or file bytes changed before processing?
- Can later results be reproduced from a known raw-data baseline?

### How this stage solves them

`src/verify_sources.py` checks every supplied file against
`DATASET_MANIFEST.json` using row counts and SHA256 hashes. `.gitattributes`
protects raw CSV/JSONL bytes from line-ending conversion, while the project
environment and dependencies make the later pipeline reproducible.

### Why it is needed

`DATASET_MANIFEST.json` provides expected row counts and SHA256 checksums. The
source includes deliberately malformed JSONL records, so source integrity and
record validity must remain separate concerns.

### Files

| File | Role |
| --- | --- |
| `requirements.txt` | Python dependency definition |
| `.gitignore` | Excludes environments, secrets and generated databases |
| `.gitattributes` | Preserves raw CSV/JSONL files byte-for-byte across operating systems |
| `src/verify_sources.py` | Checks supplied row counts and SHA256 checksums |
| `src/__init__.py` | Makes `src` importable as a Python package |

### Run

```powershell
python -m src.verify_sources
```

Expected final result:

```text
SUCCESS: All supplied source files match DATASET_MANIFEST.json.
```

---
### Validation / tests

Stage 1 has no separate pytest test file. Its validation command successfully
verified all 12 supplied files against the expected row counts and SHA256 hashes.


---

## Stage 2 — Persistent typed reference layer

### Aim

Create a persistent DuckDB database and load the six static/reference CSV
sources into typed tables.

The visit-change JSONL batches are deliberately excluded from this stage because
they require dedicated duplicate, validation, version and delete logic.

### Questions answered

- What stable companies, contractors, properties, incidents and report weeks exist?
- Can source strings and timestamps be represented with explicit database types?
- Are the reference relationships internally consistent?

### How this stage solves them

`sql/schema.sql` defines typed `src_*` tables and `src/load_reference_data.py`
loads the six reference sources inside one transaction. Row counts and logical
references are checked before commit, producing the persistent DuckDB foundation
used by later stages.

### Why this stage exists

Later visit processing needs reliable reference entities for property,
contractor, company and incident relationships. Loading these stable CSV sources
first keeps reference-data validation separate from the more complicated
versioned visit-event logic.

### Database

Generated locally at:

```text
data/modelled/onside.duckdb
```

The database file is excluded from Git because it is fully rebuildable from the
committed raw data and code.

### Source tables

| Table | Grain | Primary key |
| --- | --- | --- |
| `src_companies` | One managing company | `company_id` |
| `src_contractors` | One contractor | `contractor_id` |
| `src_properties` | One property | `property_id` |
| `src_property_management` | One management observation | `observation_id` |
| `src_incidents` | One incident | `incident_id` |
| `src_report_weeks` | One requested report week | `week_start` |

### Stage 2 files and how they connect

```text
sql/schema.sql
      |
      v
src/database.py
      |
      +---- creates/connects to data/modelled/onside.duckdb
      |
      v
src/load_reference_data.py
      |
      +---- reads the six supplied CSV files
      +---- loads typed source tables in one transaction
      +---- validates counts and logical references
      |
      v
tests/test_reference_data.py
      |
      +---- independently checks the loaded source layer
```

File responsibilities:

| File | Responsibility |
| --- | --- |
| `sql/schema.sql` | Defines the typed DuckDB source tables |
| `src/database.py` | Central database path, connection and schema setup |
| `src/load_reference_data.py` | Loads and validates all six static/reference sources atomically |
| `tests/test_reference_data.py` | Automated checks for counts, references, severity and report-week rules |
| `docs/DATA_MODEL.md` | Documents grains, keys and source-layer design choices |
| `pytest.ini` | Makes project-root modules available consistently when running pytest |

### Run Stage 2

First verify the immutable raw layer:

```powershell
python -m src.verify_sources
```

Then rebuild the typed reference layer:

```powershell
python -m src.load_reference_data
```

Then run its automated tests:

```powershell
python -m pytest tests/test_reference_data.py -q
```

Expected reference counts:

```text
src_companies                3
src_contractors              8
src_properties              24
src_property_management     62
src_incidents              247
src_report_weeks             6
```

The loader runs inside one transaction. If loading or validation fails before
commit, DuckDB rolls back instead of leaving a partially rebuilt reference layer.

---
### Validation / tests

```text
Stage-specific tests: 11 passed
Cumulative tests after Stage 2: 11 passed
```

The tests cover reference row counts, logical relationships, severity values and
report-week rules.


---

## Stage 3 — Manifest-driven visit batch landing

### Aim

Land the six visit-change files in the exact order declared by `manifest.json`
while preserving every physical input line and recording durable batch progress.

This stage intentionally **does not parse or clean JSON yet**. The source contains
deliberately malformed lines, so the raw landing layer must retain those lines for
the next technical-validation stage.

### Questions answered

- Which visit batches arrived, and in what manifest order?
- What exact physical source line came from which batch and line number?
- Can a batch be replayed or restarted without duplicating or partially landing data?

### How this stage solves them

`src/land_visit_batches.py` reads `manifest.json`, records each batch in
`ingestion_batches`, and preserves every physical JSONL line in `raw_visit_lines`.
Each batch lands transactionally; an already-landed batch is a no-op on replay,
and a failed attempt can restart without partial raw rows.

### Why this stage exists

The assignment requires:

- initial processing in manifest order;
- stable batch IDs and contents;
- safe replay of an already processed batch;
- batch progress and useful loading counts;
- evidence of invalid source lines;
- restart after failure without partial application.

Stage 3 establishes the durable raw/audit foundation for those requirements.

### New database tables

| Table | Grain | Purpose |
| --- | --- | --- |
| `ingestion_batches` | One manifest batch | Batch identity, order, source hash, status, attempts and loading/quality counters used across later stages |
| `raw_visit_lines` | One physical batch line | Preserve raw source text and exact line location before JSON parsing |

A successful Stage 3 batch has status `landed`. It will become `completed` only
in later stages after validation and business-state processing succeed.

### Stage 3 files and flow

```text
manifest.json
     |
     v
src/land_visit_batches.py
     |
     +---- verifies stable batch identity/content
     +---- records processing attempt
     +---- lands every physical JSONL line transactionally
     +---- marks batch landed / failed
     |
     v
DuckDB
  ingestion_batches
  raw_visit_lines
     |
     v
tests/test_batch_landing.py
```

| File | Responsibility |
| --- | --- |
| `sql/schema.sql` | Adds batch-audit and raw-line tables |
| `src/database.py` | Supports the normal database and isolated temporary test databases |
| `src/land_visit_batches.py` | Reads manifest order, lands batches, prevents replay changes and supports restart |
| `tests/test_batch_landing.py` | Checks counts, order, replay no-op and simulated failure/restart |
| `docs/DATA_MODEL.md` | Documents the new landing grains and lifecycle |

### Run Stage 3

The Stage 2 reference layer should already exist.

Land the visit batches:

```powershell
python -m src.land_visit_batches
```

Expected totals:

```text
6 manifest batches
1,997 raw visit input lines
```

Run the Stage 3 tests:

```powershell
python -m pytest tests/test_batch_landing.py -q
```

Then rerun the landing command:

```powershell
python -m src.land_visit_batches
```

The second run should show each batch as `skipped`. That demonstrates replay is
a no-op at this layer.

### Important boundary

Stage 3 answers:

> What exact batch lines arrived, in what batch/order, and can I land them safely?

Stage 4 answers:

> Which of those lines are technically valid JSON events, and why are invalid
> lines rejected?

Duplicate `event_id`, `source_version`, deletes and current visit state are handled
after technical validation, not silently mixed into raw landing.
### Validation / tests

```text
Stage-specific tests: 6 passed
Cumulative tests after Stage 3: 17 passed
```

The tests cover manifest order, exact raw-line counts, line numbering, replay
no-op behaviour and rollback/restart after a simulated batch failure.


---

## Stage 4 — Technical visit-event validation

### Aim

Parse every landed JSONL line and separate technically valid visit-change events
from technically invalid input while retaining evidence for every rejected line.

This stage does **not** yet decide which event wins for a visit and does not check
business references or business duration.

### Questions answered

- Which landed lines are technically valid visit-change events?
- Which lines are rejected, and exactly why?
- Can invalid lines be retained as evidence without blocking valid lines?

### How this stage solves them

`src/validate_visit_events.py` parses each raw line and validates identifiers,
versions, operations, timestamp shape and required payload fields. Valid
deliveries go to `validated_visit_events`; invalid lines go to
`visit_event_rejects` with batch, line, raw payload and reason.

### Why this stage exists

The assignment requires technically invalid events to be recorded with batch ID,
line number and an explanation while valid events continue to load.

### New tables

| Table | Grain | Purpose |
| --- | --- | --- |
| `validated_visit_events` | One technically valid physical event-delivery line | Typed source event ready for deduplication/version logic |
| `visit_event_rejects` | One technically invalid physical input line | Original payload plus rejection codes and explanation |

### Flow

```text
raw_visit_lines
      |
      v
src/validate_visit_events.py
      |
      +----------------------+
      |                      |
      v                      v
validated_visit_events   visit_event_rejects
      |
      +----> reports/Report.txt
      |
      v
tests/test_visit_validation.py
```

### Files

| File | Responsibility |
| --- | --- |
| `sql/schema.sql` | Adds technically validated-event and reject tables |
| `src/validate_visit_events.py` | Implements technical JSON/event validation and prints reject evidence |
| `src/audit_report.py` | Rebuilds the cumulative engineering/audit `reports/Report.txt` |
| `tests/test_visit_validation.py` | Checks expected counts, rejection categories and replay |
| `docs/DATA_MODEL.md` | Adds Stage 4 to the cumulative data-model documentation |

### Run

```powershell
python -m src.validate_visit_events
```

Expected totals:

```text
Technically valid event lines:   1979
Technically invalid event lines: 18
```

After the summary, the command prints the 18 rejected records in a compact ASCII table with:

```text
source file + line number
batch ID
reject category
reason codes
human-readable reason
raw source line
```

The same evidence is written cumulatively to:

```text
reports/Report.txt
```

`Report.txt` uses fixed-width ASCII tables so it remains readable in PowerShell, GitHub and plain-text editors. It is the engineering/audit report. The later
`reports/operations_report.html` is the separate business-facing report.

Then:

```powershell
python -m pytest tests/test_visit_validation.py -q
python -m pytest -q
```

Rerun:

```powershell
python -m src.validate_visit_events
```

The second validation run should show all batches as `skipped`.

### Validation / tests

```text
Stage-specific tests: 12 passed
Cumulative tests after Stage 4: 29 passed
```

The tests cover technical-valid/invalid counts, rejection categories, reject
evidence, timestamp validation and validation replay.

### Boundary

Stage 4 answers:

> Is this physical source line a technically valid visit-change event?

Stage 5 answers:

> Is it a repeated event delivery, which `source_version` wins for the visit, and
> should the visit be upserted, deleted or reactivated?

---

## Stage 5 — Deduplication, version precedence and current visit state

### Aim

Stage 5 converts the technically valid event deliveries from Stage 4 into a trustworthy event history and one latest state per visit. Here, “technically valid” means the JSON structure, required fields, types and timestamps are valid enough for the system to understand the event, but that does not mean the delivery is unique or that it is the newest version of the visit. The same `event_id` may be delivered more than once, and several different events may describe different `source_version`s of the same `visit_id`. This stage therefore deduplicates repeated event deliveries, keeps every unique event for audit, applies only a strictly newer `source_version` to the current visit state, preserves older/equal versions as stale history, and keeps delete tombstones so an older upsert cannot incorrectly restore a deleted visit.

### Questions answered

- Which technically valid deliveries are repeated `event_id`s?
- Which unique event is newer or stale for each `visit_id`?
- What is the latest active/deleted state of every visit?
- Can an older upsert incorrectly resurrect a deleted visit?

### How this stage solves them

`src/process_visit_state.py` deduplicates globally by `event_id`, preserves
duplicate evidence, and applies only a strictly higher `source_version` to
`visit_current_state`. Deletes remain as tombstones, while a genuinely newer
upsert may reactivate a visit. Unique stale/equal-version events remain in
`visit_events` for audit.

### Why this stage exists

A delivered line is not necessarily a new business event or a new visit.

- the same `event_id` may be delivered repeatedly;
- different unique events may refer to the same `visit_id`;
- lower versions can arrive after higher versions;
- deletes must remain as tombstones;
- a genuinely newer upsert may reactivate a deleted visit.

The precedence rule is:

```text
highest source_version per visit_id wins
```

### New tables

| Table | Grain | Purpose |
| --- | --- | --- |
| `visit_events` | One globally unique `event_id` | Deduplicated event history plus applied/stale outcome |
| `visit_event_duplicates` | One repeated physical delivery | Duplicate evidence linked to the canonical first event |
| `visit_current_state` | One `visit_id` | Highest-version current state, including delete tombstones |

### Flow

```text
validated_visit_events
          |
          v
src/process_visit_state.py
          |
          +-----------------------------+
          |              |              |
          v              v              v
     visit_events   duplicate audit   visit_current_state
          |
          +----> reports/Report.txt
          |
          v
tests/test_visit_state.py
```

### Expected results

```text
1,979 technically valid physical deliveries
1,944 unique event IDs
35 duplicate deliveries

1,890 applied state changes
54 stale/equal-version unique events

1,618 current visit states
1,567 active visits
51 deleted visits
```

The console and cumulative `reports/Report.txt` show:

- per-batch unique/duplicate/applied/stale counts;
- every duplicate delivery with duplicate and canonical source references;
- every stale/equal-version unique event with source reference and reason;
- V02/V03/V04 version/delete examples;
- deleted current-state tombstones in the report.

### Run

```powershell
python -m src.process_visit_state
```

Then:

```powershell
python -m pytest tests/test_visit_state.py -q
python -m pytest -q
```

Finally rerun:

```powershell
python -m src.process_visit_state
```

The second run should show every batch as `skipped`.

### Validation / tests

```text
Stage-specific tests: 11 passed
Cumulative tests after Stage 5: 40 passed
```

The tests cover event deduplication, source-version precedence, V02 tombstone
protection, V04 reactivation, V03 correction, replay and rollback/restart.

### Boundary

Stage 5 answers:

> Which deliveries are repeated events, and what is the highest-version source
> state of each visit?

Stage 6 answers:

> Is each latest active visit valid for business reporting?

---

## Stage 6 — Business quality on current active visits

### Aim

Stage 6 checks whether each latest active visit is suitable for business reporting, which is different from being technically valid. A record may have valid JSON, valid field types and valid timestamps, yet still contain a business-quality problem, such as a `property_id` that does not exist in the property reference table, an unknown `contractor_id`, or a completed visit whose `ended_at` is earlier than `started_at`. These records are not treated as malformed and are not deleted; they are preserved with exact source evidence in `visit_quality_issues` and excluded from `reportable_current_visits`. Deleted visits remain separate tombstones, while unfinished visits with `ended_at = NULL` remain valid for later reporting.

### Questions answered

- Which latest active visits are structurally valid but unsuitable for business metrics?
- Does a visit reference an unknown property or contractor?
- Does a completed visit end before it starts?
- Are deleted and unfinished visits handled separately from quality failures?

### How this stage solves them

`src/check_visit_quality.py` checks the latest active visit state against the
reference tables and duration rule. Problems are preserved in
`visit_quality_issues` with source evidence, while
`reportable_current_visits` exposes only active visits that pass the business
checks. Deleted visits stay tombstones and unfinished visits remain valid.

### Why this stage exists

A record can be valid JSON and still be unsuitable for business metrics.

The assignment explicitly defines these as business-quality issues:

```text
unknown property_id
unknown contractor_id
ended_at earlier than started_at
```

They are preserved for audit but excluded from visit metrics.

Deleted visits are **not** quality failures. Unfinished visits with
`ended_at = NULL` are valid.

### New model objects

| Object | Grain | Purpose |
| --- | --- | --- |
| `visit_quality_issues` | One issue per visit + issue code | Preserve current business-quality exceptions and their source evidence |
| `reportable_current_visits` | One active quality-valid current visit | Clean input for later analytical visit facts |

### Expected results

```text
1,618 current visit states
51 deleted tombstones

1,567 active current visits
3 business-invalid active visits
1,564 reportable active visits
```

Expected exceptions:

```text
V11  end_before_start
V12  unknown_property
V13  unknown_contractor
```

For `V11`, the console and `reports/Report.txt` show the direct timestamp proof:

```text
started_at = 2026-03-25T01:00:00Z
ended_at   = 2026-03-25T00:00:00Z
duration   = -60.0 minutes
```

### Run

```powershell
python -m src.check_visit_quality
```

Then:

```powershell
python -m pytest tests/test_visit_quality.py -q
python -m pytest -q
```

The command also extends the cumulative:

```text
reports/Report.txt
```

with issue counts, exact source references and exclusion reasons.

### Validation / tests

```text
Stage-specific tests: 10 passed
Cumulative tests after Stage 6: 50 passed
```

The tests cover V11/V12/V13 business-quality rules, reportable visit counts,
unfinished visits, deleted tombstones, cumulative report evidence and idempotency.

### Boundary

Stage 6 answers:

> Which latest active visits are suitable for business metrics?

Stage 7 answers:

> Which company managed each property at each point in time?

---

## Stage 7 — Property-management history

### Aim

Stage 7 turns the 62 unsorted property-management observations into a reliable
business-time history. A source observation says which company manages a
property from `effective_at`, but repeated observations can describe the same
unchanged manager. This stage collapses only consecutive unchanged states,
preserves a later return to a previous company as a separate interval, and
creates `[valid_from, valid_to)` periods so a visit or incident can later be
assigned to the company that actually managed the property when it occurred.
It also creates a separate current-management lookup, because today's manager
can differ from the company responsible for an older activity record.

### Questions answered

- What company managed each property at any given business timestamp?
- Which management observations represent real changes versus unchanged repeats?
- Does returning to an earlier company create a new historical period?
- Which company currently manages each property?
- Which company owns the exact transfer timestamp?

### How this stage solves them

`src/build_management_history.py` sorts observations by property and
`effective_at`, detects company changes with window functions, collapses each
consecutive unchanged run, and uses the next change time as `valid_to`.
`property_management_intervals` stores the history and
`current_property_management` exposes the single open interval for each property.
The cumulative audit report shows the full interval history and exact boundary
examples.

### Why this stage exists

A direct join from visits/incidents to `property_management` on `property_id`
would create multiple matches because each property can have several management
observations. Historical reporting therefore needs effective-dated intervals,
not merely the latest company or a property-only join.

### New model objects

| Object | Grain | Purpose |
| --- | --- | --- |
| `property_management_intervals` | One contiguous manager interval per property | Historical company attribution using business time |
| `current_property_management` | One current row per property | Explicit current manager lookup with names |

### Expected results

```text
62 source observations
37 historical intervals
25 unchanged observations collapsed
24 properties
24 current-management rows
```

### What the Stage 7 totals mean

| Result | Meaning |
| --- | --- |
| `62 source management observations` | The original rows in `src_property_management`; each is a source statement that a company manages a property from an `effective_at` time. |
| `37 historical management intervals` | The real contiguous management periods after consecutive unchanged observations are collapsed. |
| `25 unchanged observations collapsed` | `62 - 37 = 25` repeated observations confirmed an already-current manager and therefore did not start a new business interval. |
| `24 properties represented` | All 24 properties have management history. |
| `24 current management rows` | Each property has exactly one open interval with `valid_to IS NULL`, representing its current manager. |

These counts do not mean 25 observations were deleted. Their evidence is retained
through `source_observation_count` on the reconstructed interval.

Important examples:

```text
P01: C01 -> C02 -> C01
```

The final `C01` is a new interval, not a continuation of the first one.

For `P03`, management changes at exactly:

```text
2026-03-29T13:00:00Z
```

The interval rule is:

```text
valid_from <= business_time < valid_to
```

so:

```text
12:59 -> C02
13:00 -> C03
```

### Run

```powershell
python -m src.build_management_history
```

Then:

```powershell
python -m pytest tests/test_management_history.py -q
python -m pytest -q
```

The same cumulative:

```text
reports/Report.txt
```

is extended with all intervals, the P01 history, P03 boundary proof and current
management lookup.

### Validation / tests

```text
Stage-specific tests: 10 passed
Cumulative tests after Stage 7: 60 passed
```

The tests cover the 62-to-37 interval reconstruction, one current manager per
property, P01 `C01 -> C02 -> C01`, P03 exact transfer boundary, incident
attribution, positive interval duration, idempotent rebuild and audit-report
evidence.

### Boundary

Stage 7 answers:

> Who managed this property at a particular business time?

Stage 8 uses that answer to build visit and incident facts and assign the correct
historical company to every reportable activity record.

---

## Stage 8 — Analytical visit and incident facts

### Aim

Stage 8 turns the cleaned latest visit state and incident source into analytics-ready
facts. Each reportable visit becomes exactly one `fact_visits` row and each
incident becomes exactly one `fact_incidents` row. The stage assigns the company
that managed the property when the activity happened, converts the business time
into the property's IANA timezone to determine the correct local Monday reporting
week, and calculates completed visit duration from elapsed UTC time so daylight
saving does not distort hours. Visits and incidents intentionally stay as separate
fact grains because the source provides no direct key between them.

### Questions answered

- Which managing company was responsible when each visit or incident occurred?
- Which local reporting week does each activity record belong to?
- How many elapsed hours did a completed visit actually last?
- How are unfinished visits represented?
- Which otherwise-valid records fall outside the six requested report weeks?
- Can visits and incidents be combined later without creating row multiplication?

### How this stage solves them

`src/build_analytical_facts.py` joins reportable visits and incidents separately to
property timezone and historical management intervals. It derives local timestamps,
Monday week labels and requested-week flags, then materialises `fact_visits` and
`fact_incidents`. Visit duration uses the difference between the original
`TIMESTAMPTZ` instants rather than local wall-clock timestamps.

### Deterministic date/time settings

Every DuckDB connection explicitly uses:

```text
TimeZone = UTC
Calendar = gregorian
```

This prevents the machine's regional/calendar settings from changing timestamp
display or local-week calculations. Property-local conversion is still performed
explicitly with each property's IANA timezone (for example
`Pacific/Auckland` or `Australia/Sydney`).

### Why this stage exists

The final report needs reliable business facts, not raw events or current-state
tables. Historical company attribution, local-week logic and elapsed duration are
all business rules that should be resolved once in the analytical layer rather
than repeatedly inside charts. Keeping the visit and incident facts separate also
prevents accidental many-to-many fanout before aggregation.

### New model objects

| Object | Grain | Purpose |
| --- | --- | --- |
| `fact_visits` | One reportable current visit | Historical company, local week, visitor/contractor and completed-hours analytics |
| `fact_incidents` | One incident | Historical company, local week and severity analytics |

### Expected results

```text
Visit facts total                    1564
Visits in requested local weeks      1543
Visits outside requested weeks         21

Incident facts total                  247
Incidents in requested local weeks    246
Incidents outside requested weeks       1
```

Requested-week sanity metrics:

```text
Visit count                 1543
Distinct visitors            259
Completed visit hours     2053.50
Unfinished visits              91
Incident count                246
High-severity incidents        31
```

### Important proofs

Historical management:

```text
V09 / I07 at 12:59 -> C02
V10 / I06 at 13:00 -> C03
```

Property-local Monday boundary:

```text
V14 -> local Sunday 23:59 -> week 2026-03-16
V01 -> local Monday 00:00 -> week 2026-03-23
```

DST elapsed duration:

```text
V07 / V16
local wall clock: 02:30 -> 03:30
elapsed UTC time: 2.00 hours
```

### Run

```powershell
python -m src.build_analytical_facts
python -m src.build_reporting_marts
```

Then:

```powershell
python -m pytest tests/test_analytical_facts.py -q
python -m pytest -q
```

The cumulative `reports/Report.txt` is extended with fact counts, requested-week
metrics, historical-company proof, local-week proof and DST-duration proof.

### Validation / tests

Stage 8 adds 11 stage-specific tests. They cover fact grains/counts, requested
weeks, historical company attribution, local week boundaries, DST elapsed hours,
unfinished visits, requested-week metrics, idempotent rebuild and audit evidence.

After Stage 8 is confirmed locally, the expected cumulative suite is:

```text
71 passed
```

### Boundary

Stage 8 answers:

> What are the trustworthy visit and incident facts used for reporting?

Stage 9 aggregates those facts independently into company/week, property/week and
contractor reporting marts.

---


## Stage 9 — Reporting marts

### Aim

Stage 9 converts the trustworthy Stage 8 facts into small reporting tables at the
exact grains needed by the operations report. Rather than making charts repeatedly
join and aggregate raw facts, this stage computes company/week, company-period,
property/week and contractor metrics once with documented rules. Visits and
incidents are aggregated separately before metrics are combined, distinct visitors
are recalculated at each display grain, and a full property/week scaffold keeps
weeks with no activity visible.

### Questions answered

- What is the weekly activity trend for each managing company?
- What are each company's totals across the full requested period?
- Does every property appear for every requested week, including zero activity?
- Which contractors account for visit activity?
- Do mart totals reconcile to the Stage 8 facts?
- Why can distinct visitors not be added across companies or weeks?

### How this stage solves them

`src/build_reporting_marts.py` builds explicit scaffolds and independently
aggregates `fact_visits` and `fact_incidents` before joining their metrics. It
materialises `rpt_company_week`, `rpt_company_period`, `rpt_property_week` and
`rpt_contractor`. Acceptance checks reconcile visit and incident totals before the
transaction commits.

### Why this stage exists

The final report should consume stable, reviewable report grains instead of
embedding complicated business logic inside charts. Pre-aggregation makes
zero-activity rows explicit, prevents visit/incident fanout, keeps distinct counts
correct and makes the final report easier to review and regenerate.

### New model objects

| Object | Grain | Purpose |
| --- | --- | --- |
| `rpt_company_week` | One company x requested week | Weekly company trend |
| `rpt_company_period` | One company for the requested period | Period totals with direct distinct visitors |
| `rpt_property_week` | One property x requested week | Property summary including zero activity |
| `rpt_contractor` | One contractor for the requested period | Contractor visit breakdown |

### Expected results

```text
Company/week rows                  18
Company requested-period rows       3
Property/week rows                144
Contractor rows                     8
Zero-activity property/week rows   22
```

Company-period checks:

```text
C01 Clover Management  514 visits  228 visitors  639.75 hours
                       32 unfinished  89 incidents  8 high

C02 Fern Management    511 visits  222 visitors  684.75 hours
                       29 unfinished  83 incidents  14 high

C03 Wattle Management  518 visits  223 visitors  729.00 hours
                       30 unfinished  74 incidents  9 high
```

Top contractors by visit count:

```text
Field Crew 07   217
Field Crew 02   208
Field Crew 06   198
```

### Run

```powershell
python -m src.build_reporting_marts
```

Then:

```powershell
python -m pytest tests/test_reporting_marts.py -q
python -m pytest -q
```

For a complete clean rebuild through Stage 9:

```powershell
python -m src.run_pipeline --fresh
```

### Validation / tests

Stage 9 adds 12 stage-specific tests covering mart row counts, company totals,
fact reconciliation, the 144-row property/week scaffold, 22 zero-activity rows,
contractor totals/ranking, distinct-visitor non-additivity, idempotent rebuild and
audit-report evidence.

Expected cumulative suite after Stage 9:

```text
83 passed
```

### Boundary

Stage 9 answers:

> What stable aggregated datasets should the final operations report consume?

Stage 10 renders these marts into the final viewable report and committed extracts.

---

## How a reviewer should run the project

There are two useful execution modes.

**For a clean reproducibility check**, run the pipeline with one command:

```powershell
python -m src.run_pipeline --fresh
```

This removes only the generated DuckDB file and rebuilds Stages 1–9 from the
committed raw data and code.

**For the interview walkthrough**, run individual stages so each engineering
decision is visible:

```powershell
python -m src.verify_sources
python -m src.load_reference_data
python -m src.land_visit_batches
python -m src.validate_visit_events
python -m src.process_visit_state
python -m src.check_visit_quality
python -m src.build_management_history
python -m src.build_analytical_facts
python -m src.build_reporting_marts
```

The final submitted project will keep the one-command pipeline as the normal
reviewer path, while the stage commands remain useful for explanation and
debugging.

---

## Inspecting the DuckDB database

The generated database is:

```text
data/modelled/onside.duckdb
```

It is one DuckDB database file containing all tables and views created by the
pipeline. The simplest way to inspect it without installing another application is:

```powershell
python
```

Then:

```python
import duckdb

con = duckdb.connect("data/modelled/onside.duckdb")

con.sql("SHOW TABLES").show()
con.sql("DESCRIBE visit_current_state").show()
con.sql("SELECT * FROM visit_current_state LIMIT 10").show()
con.sql("SELECT * FROM visit_quality_issues").show()
```

Close the connection with:

```python
con.close()
exit()
```

A DuckDB-capable GUI database client can also open the same file directly.

---

## How to run the project currently

From the repository root:

```powershell
cd D:\project\onside\Assignment

.\.venv\Scripts\Activate.ps1

python -m src.verify_sources
python -m src.load_reference_data
python -m src.land_visit_batches
python -m src.validate_visit_events
python -m src.process_visit_state
python -m src.check_visit_quality
python -m src.build_management_history
python -m src.build_analytical_facts
python -m pytest -q
```

At the current stage this:

1. verifies the immutable raw files;
2. rebuilds and validates the typed reference layer;
3. lands all six visit-change batches in manifest order;
4. preserves all 1,997 physical JSONL lines;
5. technically validates the landed events and preserves rejects;
6. deduplicates deliveries and builds versioned current visit state;
7. checks business-quality rules and creates `reportable_current_visits`;
8. reconstructs historical and current property management;
9. builds separate visit and incident analytical facts with historical company,
   local-week and elapsed-hours logic;
10. rebuilds the cumulative engineering/audit report;
11. runs the automated regression suite.

Later stages add gold reporting models and the final business-facing HTML
operations report.

---

## Repository structure

```text
.
├── data/
│   ├── companies.csv
│   ├── contractors.csv
│   ├── incidents.csv
│   ├── properties.csv
│   ├── property_management.csv
│   ├── report_weeks.csv
│   ├── visit_changes/
│   └── modelled/                 # generated DuckDB; ignored by Git
├── docs/
│   ├── ORIGINAL_ASSIGNMENT_BRIEF.md
│   └── DATA_MODEL.md             # cumulative model documentation
├── reports/
│   └── Report.txt                # generated cumulative engineering/audit report
├── sql/
│   └── schema.sql
├── src/
│   ├── __init__.py
│   ├── audit_report.py
│   ├── build_analytical_facts.py
│   ├── build_management_history.py
│   ├── build_reporting_marts.py
│   ├── check_visit_quality.py
│   ├── database.py
│   ├── land_visit_batches.py
│   ├── load_reference_data.py
│   ├── process_visit_state.py
│   ├── run_pipeline.py
│   ├── validate_visit_events.py
│   └── verify_sources.py
├── tests/
│   ├── test_analytical_facts.py
│   ├── test_batch_landing.py
│   ├── test_management_history.py
│   ├── test_reporting_marts.py
│   ├── test_reference_data.py
│   ├── test_visit_quality.py
│   ├── test_visit_state.py
│   └── test_visit_validation.py
├── .gitattributes
├── .gitignore
├── pytest.ini
├── requirements.txt
├── manifest.json
├── DATASET_MANIFEST.json
├── BUSINESS_CONTEXT.md
├── DATA_DICTIONARY.md
├── PROJECT_GUIDANCE.md
└── README.md
```

---

## Design principles used throughout the project

- Preserve supplied raw files unchanged.
- Separate source integrity from record validity.
- Keep source, clean analytical and reporting grains explicit.
- Preserve evidence instead of silently discarding bad records.
- Make ingestion idempotent and restartable.
- Prefer correctness and explainability over unnecessary infrastructure.
- Keep generated state rebuildable from committed code and source data.
- Add tests alongside each important business rule.
- Keep the final business report understandable without requiring the reader to
  inspect Python code.

---

---

## AI / tool use

The project brief explicitly permits AI and familiar development tools. AI
assistance may be used for learning, implementation review and documentation.
All submitted code, design decisions and findings are reviewed and understood by
the candidate and will be explainable during the interview.

---
