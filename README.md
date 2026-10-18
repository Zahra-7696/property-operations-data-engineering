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
| 5. Versioning, duplicates and deletes | Deduplicate global `event_id`, apply highest `source_version` per visit and retain delete tombstones/reactivation | `visit_events`, `visit_event_duplicates`, `visit_current_state` | **In progress** |
| 6. Business data quality | Flag unknown references and invalid durations while preserving evidence | Quality-issue model | Not started |
| 7. Management history | Convert observations into historical management intervals and current management | Historical/current management models | Not started |
| 8. Analytical facts | Attribute visits/incidents historically, apply local week rules and UTC elapsed hours | Visit and incident facts | Not started |
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

### Current stage

- [ ] Deduplicate repeated global `event_id` deliveries
- [ ] Apply highest `source_version` per `visit_id`
- [ ] Preserve delete tombstones and allow genuinely newer reactivation
- [ ] Show duplicate/stale evidence in the console and cumulative `reports/Report.txt`
- [ ] Confirm Stage 5 rollback/restart and replay tests

### Next

- [ ] Add business-quality checks for unknown references and invalid duration
- [ ] Record technical rejects and loading counts
- [ ] Implement idempotent replay and restart-safe batches
- [ ] Maintain latest visit state using `source_version`
- [ ] Model property-management history
- [ ] Build visit and incident analytical facts
- [ ] Build weekly gold reporting models
- [ ] Generate the HTML operations report
- [ ] Add the complete edge-case test suite
- [ ] Add GitHub Actions CI
- [ ] Document production-scale AWS/Snowflake evolution

---

---

## Stage 1 — Setup and raw-data integrity

### Aim

Establish a reproducible project and prove that the supplied source files have
not been changed before any transformation begins.

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

---

## Stage 2 — Persistent typed reference layer

### Aim

Create a persistent DuckDB database and load the six static/reference CSV
sources into typed tables.

The visit-change JSONL batches are deliberately excluded from this stage because
they require dedicated duplicate, validation, version and delete logic.

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

---

## Stage 3 — Manifest-driven visit batch landing

### Aim

Land the six visit-change files in the exact order declared by `manifest.json`
while preserving every physical input line and recording durable batch progress.

This stage intentionally **does not parse or clean JSON yet**. The source contains
deliberately malformed lines, so the raw landing layer must retain those lines for
the next technical-validation stage.

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

---

## Stage 4 — Technical visit-event validation

### Aim

Parse every landed JSONL line and separate technically valid visit-change events
from technically invalid input while retaining evidence for every rejected line.

This stage does **not** yet decide which event wins for a visit and does not check
business references or business duration.

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

### Boundary

Stage 4 answers:

> Is this physical source line a technically valid visit-change event?

Stage 5 answers:

> Is it a repeated event delivery, which `source_version` wins for the visit, and
> should the visit be upserted, deleted or reactivated?

---

## Stage 5 — Deduplication, version precedence and current visit state

### Aim

Convert technically valid physical deliveries into:

1. one globally unique event-history row per `event_id`; and
2. one highest-version current state per `visit_id`.

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

### Boundary

Stage 5 answers:

> Which deliveries are repeated events, and what is the highest-version source
> state of each visit?

Stage 6 answers:

> Is each latest active visit valid for business reporting?

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
python -m pytest -q
```

At the current stage this:

1. verifies the immutable raw files;
2. rebuilds and validates the typed reference layer;
3. lands all six visit-change batches in manifest order;
4. preserves all 1,997 physical JSONL lines;
5. technically validates landed visit-change lines;
6. preserves all technical rejects with reasons;
7. checks replay/restart/validation behaviour through automated tests.

Later stages will add technical event validation, current-state modelling,
analytics models and report generation. A final top-level pipeline command will
eventually rebuild the complete project from a fresh checkout.

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
│   ├── database.py
│   ├── land_visit_batches.py
│   ├── load_reference_data.py
│   ├── process_visit_state.py
│   ├── validate_visit_events.py
│   └── verify_sources.py
├── tests/
│   ├── test_batch_landing.py
│   ├── test_reference_data.py
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
