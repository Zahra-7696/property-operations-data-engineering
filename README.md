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
| Pipeline explorer | Streamlit | Visual stage navigation, stage execution, table inspection, read-only SQL and audit evidence |

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
| 9. Reporting marts | Build company/week, company-period, property/week and contractor reporting marts | `rpt_company_week`, `rpt_company_period`, `rpt_property_week`, `rpt_contractor` | **Done** |
| 10. Operations report | Build a polished report with KPIs, trends, summaries, findings and quality/load evidence | `reports/operations_report.html`, `reports/extracts/` | **Done** |
| 11. CI and reproducibility | Rebuild and validate the complete project automatically from a clean checkout | `.github/workflows/ci.yml`, `src/acceptance_check.py` | **Done** |
| 12. Production design | Document how the local solution could evolve for millions of records with security, monitoring, CI/CD and versioning | `docs/PRODUCTION_DESIGN.md` | **Done** |
---

The repository implementation is complete through **Stage 12**. Interview preparation is intentionally kept outside the repository.

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

### Completed in Stage 9

- [x] Build 18 company/week trend rows
- [x] Build company requested-period totals with direct distinct counts
- [x] Build all 144 property/week rows, including 22 zero-activity weeks
- [x] Build the 8-row contractor breakdown
- [x] Aggregate visits and incidents separately before combining metrics
- [x] Prove distinct visitors are non-additive
- [x] Confirm Stage 9 tests pass (`12 passed`)
- [x] Confirm full regression suite passes (`83 passed`)
- [x] Confirm a clean `--fresh` rebuild completes through Stage 9

### Completed in Stage 10

- [x] Generate the self-contained HTML operations report
- [x] Export five useful modelled CSV extracts
- [x] Include requested period, definitions and latest successful batch
- [x] Include company trend, complete property summary and contractor breakdown
- [x] Include data-quality/load evidence
- [x] Include findings, operational follow-up and explicit limitations
- [x] Record successful report-build metadata
- [x] Add the interview-friendly pipeline walkthrough
- [x] Confirm Stage 10 tests pass (`8 passed`)
- [x] Confirm full regression suite passes (`91 passed`)

### Completed in Stage 11

- [x] Add a GitHub Actions workflow for push, pull request and manual runs
- [x] Rebuild the complete Stage 1–10 project on a clean Python 3.11 runner
- [x] Run a compact final acceptance gate after the clean rebuild
- [x] Run the full pytest regression suite in CI
- [x] Confirm the pipeline does not modify committed raw data
- [x] Upload generated HTML/audit/CSV artifacts from successful CI runs
- [x] Add lightweight tests for the CI/reproducibility configuration
- [x] Confirm final acceptance checks pass
- [x] Confirm Stage 11 tests pass (`5 passed`)
- [x] Confirm full regression suite passes (`96 passed`)

### Completed in Stage 12

- [x] Document a production-scale design for millions of records
- [x] Explain batch vs streaming and why row volume alone does not require Kafka
- [x] Explain incremental/idempotent event processing and replay
- [x] Document AWS and Snowflake security controls
- [x] Document infrastructure and data-quality monitoring
- [x] Explain CI/CD promotion and rollback
- [x] Distinguish business, event, code/deployment and schema versioning
- [x] Keep the production document reviewer-facing and independent of Onside's actual architecture

### Pipeline Explorer

The repository includes a Streamlit-based **Pipeline Explorer** for visual
inspection and demonstration of the completed Stage 1–12 data flow. It is a
presentation/control layer over the existing pipeline modules; the transformation
logic remains in the normal Python/SQL implementation.

The explorer provides four separate workspaces so stage-level results, database
inspection and engineering evidence do not crowd the same page:

```text
Overview | Tables | Queries & Audit | Report.txt Evidence
```

**Overview** explains each stage's aim and implementation files, runs the existing
stage module, shows the important result counts and reports the already-verified
automated-test checkpoint without rerunning pytest during a live walkthrough.

**Tables** lists the tables/views created by the selected stage. Selecting an
object explains its grain, why it exists and its expected result, shows the DDL
from `sql/schema.sql`, and provides an editable read-only query that defaults to:

```sql
SELECT *
FROM "<selected_object>"
LIMIT 10;
```

**Queries & Audit** is intentionally curated so it does not duplicate the **Tables**
workspace. Routine `SELECT * LIMIT 10` previews remain under **Tables**; this
workspace keeps the queries that prove a rule, reconcile counts or support a useful
business drill-down. Examples include deriving duplicate `event_id` deliveries
directly from `validated_visit_events`, delete tombstones, source-version
precedence, business-quality failures, historical management lookup by
property/timestamp or company/time range, property-local week/DST checks,
requested-period inclusion, incident severity, distinct-visitor semantics, and
report KPI reconciliation. Stage 10 also provides editable company/date-range and
contractor/date-range visit drill-downs showing visitor, contractor, property,
historical company, completed/unfinished status and known completed hours.

**Report.txt Evidence** is a separate view of the cumulative engineering audit
file. It shows only Stage 1 through the currently selected stage, marks which of
those stage sections are present, and allows the selected stage's evidence or the
cumulative evidence through that stage to be inspected. Selecting a stage in the
navigation does not itself execute that stage.

For Stage 1, select the **project repository root**, not only the `data/`
directory. The root should contain `DATASET_MANIFEST.json`, `manifest.json`,
`data/`, `src/`, `sql/` and `reports/`. Missing, unreadable or modified source
files are reported clearly.

The explorer also opens the generated `reports/operations_report.html` from the
report/CI stages while keeping the business report separate from the engineering
audit trail.

The SQL interface is intentionally read-only. It accepts inspection statements
such as `SELECT`, `WITH`, `SHOW`, `DESCRIBE` and `EXPLAIN` and rejects
state-changing SQL.

---

## How a reviewer should run the project

Run commands from the **repository root**. After creating/activating the Python
environment, install the project dependencies:

```powershell
pip install -r requirements.txt
```

### Mode 1 — Recommended visual pipeline walkthrough

First install the requirements as shown above. The recommended interview/reviewer experience is the Streamlit Pipeline Explorer:

```powershell
streamlit run src/pipeline_explorer.py
```

The left side contains Stages 1–12. The selected stage is highlighted in the
pipeline diagram directly below the stage title. For each stage a reviewer can:

1. read the stage aim and implementation-file responsibilities;
2. run the existing stage module and inspect its result summary;
3. inspect every table/view created by the stage and its creation SQL;
4. edit and run read-only inspection/audit queries;
5. trace concrete records through versioning, historical management, facts and marts;
6. inspect `reports/Report.txt` evidence through the currently selected stage;
7. open the final HTML operations report from the report/CI stages.

The stage buttons run the selected data stage but do **not** rerun pytest during the visual walkthrough. They report the previously verified stage/cumulative test checkpoint so the live demo stays responsive. Use **Tables** for object/DDL/row inspection, **Queries & Audit** for curated rule and business drill-down queries, and **Report.txt Evidence** for cumulative engineering evidence.

### Mode 2 — Full terminal pipeline

For the complete interactive command-line walkthrough:

```powershell
python -m src.run_pipeline --fresh
```

This removes only the generated DuckDB state and rebuilds the complete core
project from the committed source files through the final Stage 10 report.

The runner is intentionally interactive. For each stage it:

1. explains the aim in plain language;
2. lists the questions that stage answers;
3. runs the transformation;
4. explains and displays the important result tables;
5. states the relevant verified test coverage;
6. prints a separator;
7. waits for Enter before continuing.

For CI or a clean rebuild without pauses, use the unattended form:

```powershell
python -m src.run_pipeline --fresh --no-pause
```

### Mode 3 — Run an individual stage

Individual stage commands remain useful for focused explanation or debugging:

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
python -m src.generate_report
```

The final viewable business artifact is:

```text
reports/operations_report.html
```

The cumulative engineering/audit evidence is:

```text
reports/Report.txt
```

For reporting-period interpretation, `src_report_weeks` contains the requested
property-local Monday week labels. `fact_visits.week_start_local` and
`fact_incidents.week_start_local` are matched to that list through
`is_requested_week`. Incident severity is source-provided as `low`, `medium` or
`high`; `is_high` is derived as true exactly when `severity = 'high'`.

### Final automated verification

After a rebuild, run the final acceptance gate:

```powershell
python -m src.acceptance_check
```

Then run the complete regression suite:

```powershell
python -m pytest -q
```

The verified final repository state is:

```text
Final acceptance check: passed
Stage 11 CI/reproducibility tests: 5 passed
Full regression suite: 96 passed
```

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
│   │   ├── batch_01.jsonl
│   │   ├── batch_02.jsonl
│   │   ├── batch_03.jsonl
│   │   ├── batch_04.jsonl
│   │   ├── batch_05.jsonl
│   │   └── batch_06.jsonl
│   └── modelled/                     # generated DuckDB; ignored by Git
├── .github/
│   └── workflows/
│       └── ci.yml                    # clean rebuild + acceptance + pytest
├── docs/
│   ├── ORIGINAL_ASSIGNMENT_BRIEF.md
│   ├── DATA_MODEL.md                 # cumulative model documentation
│   └── PRODUCTION_DESIGN.md          # production-scale architecture and operations
├── reports/
│   ├── Report.txt                    # cumulative engineering/audit report
│   ├── operations_report.html        # final viewable business report
│   ├── extracts/
│   │   ├── company_week.csv
│   │   ├── company_period.csv
│   │   ├── property_week.csv
│   │   ├── contractor.csv
│   │   └── data_quality_summary.csv
│   └── templates/
│       └── operations_report.html.j2
├── sql/
│   └── schema.sql
├── src/
│   ├── __init__.py
│   ├── acceptance_check.py
│   ├── audit_report.py
│   ├── build_analytical_facts.py
│   ├── build_management_history.py
│   ├── build_reporting_marts.py
│   ├── check_visit_quality.py
│   ├── database.py
│   ├── generate_report.py
│   ├── land_visit_batches.py
│   ├── load_reference_data.py
│   ├── pipeline_explorer.py           # visual stage/table/SQL/audit explorer
│   ├── pipeline_presenter.py
│   ├── process_visit_state.py
│   ├── query_examples.py              # read-only stage query catalogue
│   ├── run_pipeline.py
│   ├── validate_visit_events.py
│   └── verify_sources.py
├── tests/
│   ├── test_analytical_facts.py
│   ├── test_batch_landing.py
│   ├── test_ci_reproducibility.py
│   ├── test_management_history.py
│   ├── test_reference_data.py
│   ├── test_report.py
│   ├── test_reporting_marts.py
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

## AI / tool use

The project brief explicitly permits AI and familiar development tools. AI
assistance may be used for learning, implementation review and documentation.
All submitted code, design decisions and findings are reviewed and understood by
the candidate and will be explainable during the interview.

---
