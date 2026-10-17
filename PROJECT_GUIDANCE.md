# Project repository and interview

Work on this project at home, then share a Git repository before the interview.
There is no elapsed-time limit. Agree the submission date with your interviewer.
State assumptions and unfinished work clearly. The core project is sufficient;
optional extensions are not expected.

## What to commit

- All supplied synthetic raw data, unchanged, including deliberately malformed lines.
- Loading/transformation code, model definitions and dependency information.
- Meaningful automated tests and instructions to run them.
- Useful modelled data extracts and a report or screenshots we can inspect.
- A README covering the business problem, execution, grains/keys, quality findings,
  report definitions, tradeoffs, limitations and tools used.

Choose a structure that suits your tools. For example:

```text
README.md
data/raw/          # supplied source exports
data/modelled/     # useful small extracts
src/ or models/   # ingestion and transformations
tests/
reports/
dependency file
.gitignore
```

Explain how someone can rebuild the project from a fresh checkout. SQL and a local
store are sufficient. **SQLite or DuckDB would be a good choice for the local
database**, but you are welcome to use other tools. Notebooks, dbt and BI tools
are welcome when useful. Cloud hosting and a custom app are optional.
There are no prescribed function names or
exact output schemas. Add tests that would catch plausible data or processing errors.

Make the report reviewable without a paid account: a committed HTML report,
spreadsheet, notebook with saved outputs, or exported screenshots/charts with
underlying queries are acceptable. Document how to regenerate it.

Keep virtual environments, downloaded dependencies, generated database binaries,
credentials and machine-specific paths out of the repository. The supplied data
is fictional and intended to be committed. A private repo is fine: share its URL
and arrange access with your interviewer. A tag identifying the submitted version
is useful but optional.

Documentation, search and AI assistance are allowed. Include a short tools-used
note and be prepared to explain your decisions and code during the interview.

## The discussion

Walk us through the repository and report. Use a few source records to explain
how changes become modelled facts and metrics. We will discuss your handling of
data quality, keys, history, retries and reporting, then explore what you would
change as requirements or data volumes grow.

We assess correct business results, clear engineering/modelling decisions, useful
reporting, validation and reproducibility. Visual polish and optional infrastructure
do not substitute for a sound core project.
