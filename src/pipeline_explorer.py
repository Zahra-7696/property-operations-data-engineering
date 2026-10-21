from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

# Streamlit executes this file as a script (for example:
# `streamlit run src/pipeline_explorer.py`). In that mode Python places the
# `src/` directory, not the repository root, on sys.path. Add the repository
# root explicitly so absolute imports such as `from src...` work reliably.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import query_examples as qe
from src.database import connect_database


DATASET_MANIFEST = PROJECT_ROOT / "DATASET_MANIFEST.json"
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
AUDIT_REPORT_PATH = PROJECT_ROOT / "reports" / "Report.txt"
HTML_REPORT_PATH = PROJECT_ROOT / "reports" / "operations_report.html"
PRODUCTION_DESIGN_PATH = PROJECT_ROOT / "docs" / "PRODUCTION_DESIGN.md"


STAGES: dict[int, dict[str, Any]] = {
    1: {
        "name": "Source integrity",
        "aim": (
            "Verify the supplied CSV/JSONL files before modelling. The check compares "
            "physical row counts and SHA256 hashes with DATASET_MANIFEST.json so every "
            "later result starts from a known, unchanged source."
        ),
        "module": None,
        "objects": [],
    },
    2: {
        "name": "Typed reference data",
        "aim": (
            "Load and validate the six CSV reference sources in typed DuckDB tables, "
            "with stable keys, relationships and source-domain checks."
        ),
        "module": "src.load_reference_data",
        "objects": [
            "src_companies",
            "src_contractors",
            "src_properties",
            "src_property_management",
            "src_incidents",
            "src_report_weeks",
        ],
    },
    3: {
        "name": "Raw batch landing",
        "aim": (
            "Land all six visit-change batches in manifest order while preserving every "
            "physical JSONL line and durable batch/replay metadata."
        ),
        "module": "src.land_visit_batches",
        "objects": ["ingestion_batches", "raw_visit_lines"],
    },
    4: {
        "name": "Technical validation",
        "aim": (
            "Parse landed JSONL lines, retain technically valid event deliveries and "
            "preserve invalid lines with source location and rejection reason."
        ),
        "module": "src.validate_visit_events",
        "objects": ["validated_visit_events", "visit_event_rejects"],
    },
    5: {
        "name": "Versioned visit state",
        "aim": (
            "Deduplicate global event_id deliveries, preserve event history and maintain "
            "one highest-source_version current state per visit, including delete tombstones."
        ),
        "module": "src.process_visit_state",
        "objects": ["visit_events", "visit_event_duplicates", "visit_current_state"],
    },
    6: {
        "name": "Business data quality",
        "aim": (
            "Separate structurally valid events from business-reportable visits by flagging "
            "unknown references and completed visits whose end precedes the start."
        ),
        "module": "src.check_visit_quality",
        "objects": ["visit_quality_issues", "reportable_current_visits"],
    },
    7: {
        "name": "Management history",
        "aim": (
            "Convert management observations into effective-dated historical intervals and "
            "keep current management separate from historical attribution."
        ),
        "module": "src.build_management_history",
        "objects": ["property_management_intervals", "current_property_management"],
    },
    8: {
        "name": "Analytical facts",
        "aim": (
            "Build separate visit and incident facts with historical company attribution, "
            "property-local Monday weeks and elapsed UTC visit duration."
        ),
        "module": "src.build_analytical_facts",
        "objects": ["fact_visits", "fact_incidents"],
    },
    9: {
        "name": "Reporting marts",
        "aim": (
            "Aggregate the fact layers into stable company/week, company-period, "
            "property/week and contractor reporting grains without fact fanout."
        ),
        "module": "src.build_reporting_marts",
        "objects": [
            "rpt_company_week",
            "rpt_company_period",
            "rpt_property_week",
            "rpt_contractor",
        ],
    },
    10: {
        "name": "Operations report",
        "aim": (
            "Generate the self-contained HTML operations report, CSV extracts and "
            "report-build metadata from the validated reporting marts."
        ),
        "module": "src.generate_report",
        "objects": ["report_builds"],
    },
    11: {
        "name": "CI and reproducibility",
        "aim": (
            "Validate that the complete repository can rebuild and pass acceptance/tests "
            "from a clean environment, rather than only on the developer's workstation."
        ),
        "module": "src.acceptance_check",
        "objects": [],
    },
    12: {
        "name": "Production design",
        "aim": (
            "Document how the same business rules could evolve for millions of records "
            "using cloud storage, decoupled ingestion, incremental warehouse processing, "
            "security, monitoring, CI/CD and versioning."
        ),
        "module": None,
        "objects": [],
    },
}


STAGE_RESULT_SQL: dict[int, str] = {
    2: """
SELECT
    (SELECT COUNT(*) FROM src_companies) AS companies,
    (SELECT COUNT(*) FROM src_contractors) AS contractors,
    (SELECT COUNT(*) FROM src_properties) AS properties,
    (SELECT COUNT(*) FROM src_property_management) AS management_observations,
    (SELECT COUNT(*) FROM src_incidents) AS incidents,
    (SELECT COUNT(*) FROM src_report_weeks) AS report_weeks;
""",
    3: """
SELECT
    COUNT(*) AS batches,
    SUM(input_lines) AS input_lines,
    SUM(CASE WHEN status IN ('landed', 'completed') THEN 1 ELSE 0 END) AS landed_or_completed
FROM ingestion_batches;
""",
    4: """
SELECT
    (SELECT COUNT(*) FROM raw_visit_lines) AS raw_lines,
    (SELECT COUNT(*) FROM validated_visit_events) AS technically_valid,
    (SELECT COUNT(*) FROM visit_event_rejects) AS technical_rejects;
""",
    5: """
SELECT
    (SELECT COUNT(*) FROM visit_events) AS unique_events,
    (SELECT COUNT(*) FROM visit_event_duplicates) AS duplicate_deliveries,
    (SELECT COUNT(*) FROM visit_events WHERE state_action = 'applied') AS applied_changes,
    (SELECT COUNT(*) FROM visit_events WHERE state_action = 'stale') AS stale_events,
    (SELECT COUNT(*) FROM visit_current_state) AS current_visits,
    (SELECT COUNT(*) FROM visit_current_state WHERE is_deleted = TRUE) AS deleted_tombstones;
""",
    6: """
SELECT
    (SELECT COUNT(*) FROM visit_current_state WHERE is_deleted = FALSE) AS active_current_visits,
    (SELECT COUNT(DISTINCT visit_id) FROM visit_quality_issues) AS business_invalid_visits,
    (SELECT COUNT(*) FROM reportable_current_visits) AS reportable_current_visits;
""",
    7: """
SELECT
    (SELECT COUNT(*) FROM src_property_management) AS source_observations,
    (SELECT COUNT(*) FROM property_management_intervals) AS management_intervals,
    (SELECT COUNT(*) FROM current_property_management) AS current_management_rows;
""",
    8: """
SELECT
    (SELECT COUNT(*) FROM fact_visits) AS visit_facts,
    (SELECT COUNT(*) FROM fact_incidents) AS incident_facts,
    (SELECT COUNT(*) FROM fact_visits WHERE is_requested_week = TRUE) AS requested_visits,
    (SELECT COUNT(*) FROM fact_incidents WHERE is_requested_week = TRUE) AS requested_incidents,
    (SELECT ROUND(SUM(COALESCE(completed_hours, 0)), 2)
       FROM fact_visits WHERE is_requested_week = TRUE) AS completed_hours,
    (SELECT SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END)
       FROM fact_visits WHERE is_requested_week = TRUE) AS unfinished_visits;
""",
    9: """
SELECT
    (SELECT COUNT(*) FROM rpt_company_week) AS company_week_rows,
    (SELECT COUNT(*) FROM rpt_company_period) AS company_period_rows,
    (SELECT COUNT(*) FROM rpt_property_week) AS property_week_rows,
    (SELECT COUNT(*) FROM rpt_property_week WHERE has_activity = FALSE) AS zero_activity_rows,
    (SELECT COUNT(*) FROM rpt_contractor) AS contractor_rows;
""",
    10: """
SELECT
    report_name,
    generated_at,
    latest_batch_id,
    first_report_week,
    last_report_week
FROM report_builds
ORDER BY generated_at DESC
LIMIT 1;
""",
    11: """
SELECT
    (SELECT COUNT(*) FROM ingestion_batches WHERE status = 'completed') AS completed_batches,
    (SELECT COUNT(*) FROM report_builds) AS report_build_rows,
    (SELECT MAX(generated_at) FROM report_builds) AS latest_report_build;
""",
}


AUDIT_QUERIES: dict[int, dict[str, str]] = {
    2: {
        "Reference source reconciliation": qe.STAGE_2_REFERENCE_COUNTS,
    },
    3: {
        "Batch loading status": qe.STAGE_3_BATCH_COUNTS,
        "Trace raw source line": qe.STAGE_3_RAW_LINE,
    },
    4: {
        "Technical rejects": qe.STAGE_4_REJECTS,
        "Valid E00275": qe.STAGE_4_VALID_EVENT,
    },
    5: {
        "V02 version/tombstone history": qe.STAGE_5_V02_EVENT_HISTORY,
        "E00002 duplicate evidence": qe.STAGE_5_V02_DUPLICATE,
        "SYN_V00256 correction history": qe.STAGE_5_CORRECTION_HISTORY,
        "SYN_V00256 correction winner": qe.STAGE_5_CORRECTION_WINNER,
    },
    6: {
        "Business quality issues": qe.STAGE_6_QUALITY_ISSUES,
        "Active / invalid / reportable reconciliation": qe.STAGE_6_COUNTS,
    },
    7: {
        "P03 management intervals": qe.STAGE_7_P03_INTERVALS,
        "P03 transfer boundary": qe.STAGE_7_P03_BOUNDARY,
    },
    8: {
        "Local week boundary": qe.STAGE_8_LOCAL_WEEK_BOUNDARY,
        "DST-safe duration": qe.STAGE_8_DST_DURATION,
        "Report KPI facts": qe.STAGE_8_REPORT_KPIS,
    },
    9: {
        "Company period mart": qe.STAGE_9_COMPANY_PERIOD,
        "Zero-activity property weeks": qe.STAGE_9_ZERO_ACTIVITY,
        "Distinct visitor non-additivity": qe.STAGE_9_DISTINCT_NON_ADDITIVE,
        "Contractor mart": qe.STAGE_9_CONTRACTORS,
    },
    10: {
        "Report build metadata": qe.STAGE_10_REPORT_BUILD,
        "Headline KPI facts": qe.STAGE_8_REPORT_KPIS,
    },
    11: {
        "Final KPI facts": qe.STAGE_8_REPORT_KPIS,
        "Report build metadata": qe.STAGE_10_REPORT_BUILD,
    },
}


SQL_EXPLORER_QUERIES: dict[int, dict[str, str]] = {
    1: {
        "Show all model tables": qe.SHOW_ALL_TABLES,
    },
    2: {
        "Reference table counts": qe.STAGE_2_REFERENCE_COUNTS,
        "Preview properties": qe.PREVIEW_SRC_PROPERTIES,
        "Preview incidents": qe.PREVIEW_SRC_INCIDENTS,
    },
    3: {
        "Trace batch_01 line 1": qe.STAGE_3_RAW_LINE,
        "Batch status": qe.STAGE_3_BATCH_COUNTS,
        "Preview raw lines": qe.PREVIEW_RAW_VISIT_LINES,
    },
    4: {
        "Valid E00275": qe.STAGE_4_VALID_EVENT,
        "Technical rejects": qe.STAGE_4_REJECTS,
        "Preview valid events": qe.PREVIEW_VALIDATED_VISIT_EVENTS,
    },
    5: {
        "V02 event history": qe.STAGE_5_V02_EVENT_HISTORY,
        "V02 current tombstone": qe.STAGE_5_V02_CURRENT_STATE,
        "E00002 duplicate": qe.STAGE_5_V02_DUPLICATE,
        "SYN_V00256 correction history": qe.STAGE_5_CORRECTION_HISTORY,
    },
    6: {
        "V11/V12/V13 quality issues": qe.STAGE_6_QUALITY_ISSUES,
        "Quality reconciliation": qe.STAGE_6_COUNTS,
        "Preview reportable visits": qe.PREVIEW_REPORTABLE_CURRENT_VISITS,
    },
    7: {
        "P03 intervals": qe.STAGE_7_P03_INTERVALS,
        "P03 12:59/13:00 boundary": qe.STAGE_7_P03_BOUNDARY,
        "Preview current management": qe.PREVIEW_CURRENT_PROPERTY_MANAGEMENT,
    },
    8: {
        "Local week boundary": qe.STAGE_8_LOCAL_WEEK_BOUNDARY,
        "DST duration": qe.STAGE_8_DST_DURATION,
        "Headline KPI facts": qe.STAGE_8_REPORT_KPIS,
        "Preview visit facts": qe.PREVIEW_FACT_VISITS_STAGE8,
        "Preview incident facts": qe.PREVIEW_FACT_INCIDENTS,
    },
    9: {
        "Company period mart": qe.STAGE_9_COMPANY_PERIOD,
        "Zero activity": qe.STAGE_9_ZERO_ACTIVITY,
        "Distinct visitors": qe.STAGE_9_DISTINCT_NON_ADDITIVE,
        "Contractors": qe.STAGE_9_CONTRACTORS,
    },
    10: {
        "Report build": qe.STAGE_10_REPORT_BUILD,
        "Company period mart": qe.STAGE_9_COMPANY_PERIOD,
        "Headline KPI facts": qe.STAGE_8_REPORT_KPIS,
    },
    11: {
        "Show final tables": qe.SHOW_ALL_TABLES,
        "Headline KPI facts": qe.STAGE_8_REPORT_KPIS,
        "Report build": qe.STAGE_10_REPORT_BUILD,
    },
    12: {
        "Show final tables": qe.SHOW_ALL_TABLES,
        "Describe visit current state": qe.DESCRIBE_VISIT_CURRENT_STATE,
        "Describe visit facts": qe.DESCRIBE_FACT_VISITS,
    },
}


def _set_page() -> None:
    st.set_page_config(
        page_title="Property Operations Pipeline Explorer",
        page_icon="🧭",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(
        """
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
[data-testid="stSidebar"] { min-width: 270px; max-width: 270px; }
.stage-title { font-size: 2rem; font-weight: 750; margin-bottom: 0.2rem; }
.stage-subtitle { opacity: 0.78; margin-bottom: 0.8rem; }
.small-note { font-size: 0.88rem; opacity: 0.75; }
.result-box {
    border: 1px solid rgba(128,128,128,.25);
    border-radius: 10px;
    padding: .8rem 1rem;
    margin-top: .5rem;
}
</style>
""",
        unsafe_allow_html=True,
    )


def _count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for _ in reader)


def _count_jsonl_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _count_rows(path: Path) -> int:
    if path.suffix.lower() == ".csv":
        return _count_csv_rows(path)
    if path.suffix.lower() == ".jsonl":
        return _count_jsonl_rows(path)
    raise ValueError(f"Unsupported source type: {path.suffix}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_source_file(selected: Path, relative_path: str) -> Path | None:
    rel = Path(relative_path)
    candidates: list[Path] = []

    # Selected path may be repository root.
    candidates.append(selected / rel)

    # Or the user may select the data/ directory itself.
    if rel.parts and rel.parts[0].lower() == "data":
        candidates.append(selected / Path(*rel.parts[1:]))

    # Or the selected directory may be a parent containing data/.
    candidates.append(selected / "data" / rel.name)

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    return None


def _find_manifest(selected: Path) -> Path | None:
    candidates = [
        selected / "DATASET_MANIFEST.json",
        selected.parent / "DATASET_MANIFEST.json",
        PROJECT_ROOT / "DATASET_MANIFEST.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def validate_source_directory(selected_path: str) -> tuple[pd.DataFrame, bool, str]:
    selected = Path(selected_path).expanduser()

    if not selected.exists() or not selected.is_dir():
        return (
            pd.DataFrame(),
            False,
            f"I can't open the selected source directory: {selected}",
        )

    manifest_path = _find_manifest(selected)
    if manifest_path is None:
        return (
            pd.DataFrame(),
            False,
            "I can't open DATASET_MANIFEST.json for this source location.",
        )

    with manifest_path.open("r", encoding="utf-8") as handle:
        expected_files: dict[str, dict[str, object]] = json.load(handle)

    rows: list[dict[str, object]] = []
    all_ok = True

    for relative_path, expected in expected_files.items():
        source_path = _resolve_source_file(selected, relative_path)

        if source_path is None:
            rows.append(
                {
                    "source_file": relative_path,
                    "actual_rows": None,
                    "expected_rows": int(expected["rows"]),
                    "row_check": "CAN'T OPEN",
                    "sha256_check": "CAN'T OPEN",
                    "status": "FAIL",
                }
            )
            all_ok = False
            continue

        try:
            actual_rows = _count_rows(source_path)
            actual_hash = _sha256(source_path)
        except OSError:
            rows.append(
                {
                    "source_file": relative_path,
                    "actual_rows": None,
                    "expected_rows": int(expected["rows"]),
                    "row_check": "CAN'T OPEN",
                    "sha256_check": "CAN'T OPEN",
                    "status": "FAIL",
                }
            )
            all_ok = False
            continue

        expected_rows = int(expected["rows"])
        expected_hash = str(expected["sha256"])
        rows_ok = actual_rows == expected_rows
        hash_ok = actual_hash == expected_hash

        rows.append(
            {
                "source_file": relative_path,
                "actual_rows": actual_rows,
                "expected_rows": expected_rows,
                "row_check": "MATCH" if rows_ok else "MISMATCH",
                "sha256_check": "MATCH" if hash_ok else "MISMATCH",
                "status": "PASS" if rows_ok and hash_ok else "FAIL",
            }
        )

        all_ok = all_ok and rows_ok and hash_ok

    message = (
        "All supplied source files match DATASET_MANIFEST.json."
        if all_ok
        else "I can't validate all source files. One or more files are missing, unreadable or do not match the manifest."
    )

    return pd.DataFrame(rows), all_ok, message


def _database_query(sql: str, max_rows: int = 2000) -> tuple[pd.DataFrame, bool]:
    connection = connect_database(read_only=True)

    try:
        cursor = connection.execute(sql)
        if cursor.description is None:
            return pd.DataFrame(), False

        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return pd.DataFrame(rows, columns=columns), truncated
    finally:
        connection.close()


def _is_read_only_sql(sql: str) -> bool:
    cleaned = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE).strip()

    if not cleaned:
        return False

    first = cleaned.split(None, 1)[0].upper()
    if first not in {"SELECT", "WITH", "SHOW", "DESCRIBE", "EXPLAIN"}:
        return False

    forbidden = re.compile(
        r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|COPY|EXPORT|IMPORT|"
        r"ATTACH|DETACH|INSTALL|LOAD|CALL|SET|PRAGMA|VACUUM)\b",
        flags=re.IGNORECASE,
    )
    return forbidden.search(cleaned) is None


def _render_query_result(sql: str, key: str) -> None:
    if not _is_read_only_sql(sql):
        st.error(
            "The Pipeline Explorer accepts read-only SQL only. "
            "Use SELECT, WITH, SHOW, DESCRIBE or EXPLAIN."
        )
        return

    try:
        frame, truncated = _database_query(sql)
    except Exception as exc:  # reviewer-facing diagnostic
        st.error(f"Query failed: {exc}")
        return

    if frame.empty:
        st.info("Query completed and returned no rows.")
        return

    st.dataframe(frame, use_container_width=True, height=430, hide_index=True)
    suffix = " (display capped at 2,000 rows)" if truncated else ""
    st.caption(f"Rows displayed: {len(frame):,}{suffix}")


def _extract_ddl(object_name: str) -> str:
    if not SCHEMA_PATH.exists():
        return "sql/schema.sql could not be opened."

    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    patterns = [
        rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{re.escape(object_name)}\s*\(.*?\);",
        rf"CREATE\s+OR\s+REPLACE\s+VIEW\s+{re.escape(object_name)}\s+AS\s+.*?;",
        rf"CREATE\s+VIEW\s+{re.escape(object_name)}\s+AS\s+.*?;",
    ]

    for pattern in patterns:
        match = re.search(pattern, schema, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(0).strip()

    return f"DDL for {object_name} was not found in sql/schema.sql."


def _run_module(module_name: str, timeout_seconds: int = 900) -> dict[str, Any]:
    command = [sys.executable, "-m", module_name]
    started = datetime.now()

    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": " ".join(command),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "started": started,
            "finished": datetime.now(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": " ".join(command),
            "returncode": -1,
            "stdout": exc.stdout or "",
            "stderr": f"Command exceeded {timeout_seconds} seconds.",
            "started": started,
            "finished": datetime.now(),
        }


def _audit_mtime() -> float | None:
    return AUDIT_REPORT_PATH.stat().st_mtime if AUDIT_REPORT_PATH.exists() else None


def _report_status(before: float | None) -> str:
    if not AUDIT_REPORT_PATH.exists():
        return "reports/Report.txt is not available yet."

    after = AUDIT_REPORT_PATH.stat().st_mtime
    if before is not None and after > before:
        return "Audit evidence refreshed: reports/Report.txt"

    return "Cumulative audit evidence available: reports/Report.txt"


def _result_dataframe(stage_number: int) -> pd.DataFrame | None:
    sql = STAGE_RESULT_SQL.get(stage_number)
    if not sql:
        return None

    try:
        frame, _ = _database_query(sql)
        return frame
    except Exception:
        return None


def _pipeline_figure(selected_stage: int) -> go.Figure:
    labels = [
        "Raw\nsources",
        "Typed\nreference",
        "Raw\nlanding",
        "Technical\nvalidation",
        "Versioned\nstate",
        "Business\nquality",
        "Management\nhistory",
        "Facts",
        "Marts",
        "HTML\nreport",
        "CI",
        "Production\ndesign",
    ]
    x = list(range(1, 13))
    sizes = [28 if i == selected_stage else 20 for i in x]

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=x,
            y=[0] * 12,
            mode="lines",
            line={"width": 2},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=[0] * 12,
            mode="markers+text",
            marker={"size": sizes},
            text=labels,
            textposition="bottom center",
            customdata=x,
            hovertemplate="Stage %{customdata}<extra></extra>",
            showlegend=False,
        )
    )

    fig.update_layout(
        height=220,
        margin={"l": 15, "r": 15, "t": 20, "b": 55},
        xaxis={"visible": False, "range": [0.4, 12.6]},
        yaxis={"visible": False, "range": [-1, 1]},
    )
    return fig


def _stage_header(stage_number: int) -> None:
    stage = STAGES[stage_number]
    st.markdown(
        f'<div class="stage-title">Stage {stage_number} — {stage["name"]}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="stage-subtitle">Property Operations Pipeline Explorer</div>',
        unsafe_allow_html=True,
    )


def _render_metric_row(frame: pd.DataFrame) -> None:
    if frame.empty:
        return

    first = frame.iloc[0]
    cols = st.columns(min(len(first), 6))

    for index, (name, value) in enumerate(first.items()):
        target = cols[index % len(cols)]
        if pd.isna(value):
            display = "—"
        elif isinstance(value, float):
            display = f"{value:,.2f}"
        elif isinstance(value, int):
            display = f"{value:,}"
        else:
            display = str(value)
        target.metric(name.replace("_", " ").title(), display)


def _run_stage(stage_number: int) -> None:
    stage = STAGES[stage_number]
    before_report = _audit_mtime()

    if stage_number == 1:
        source_path = st.session_state.get("source_directory", str(PROJECT_ROOT / "data"))
        frame, success, message = validate_source_directory(source_path)
        st.session_state["stage_1_validation"] = frame
        st.session_state["stage_1_validation_success"] = success
        st.session_state["stage_1_validation_message"] = message
        return

    module_name = stage.get("module")
    if not module_name:
        return

    result = _run_module(module_name)

    # Refresh the cumulative engineering report after successful data stages.
    # This is best-effort: a stage result remains valid even if the audit renderer
    # cannot run because later model layers have not been built yet.
    if result["returncode"] == 0 and 2 <= stage_number <= 10:
        audit_refresh = _run_module("src.audit_report")
        result["audit_refresh_returncode"] = audit_refresh["returncode"]
        if audit_refresh["returncode"] != 0:
            result["audit_refresh_warning"] = (
                "The stage completed, but the cumulative audit renderer could not "
                "refresh Report.txt at this point in the build."
            )

    result["audit_status"] = _report_status(before_report)
    st.session_state[f"stage_run_{stage_number}"] = result


def _render_run_output(stage_number: int) -> None:
    key = f"stage_run_{stage_number}"
    if key not in st.session_state:
        return

    result = st.session_state[key]
    if result["returncode"] == 0:
        st.success(f"Stage {stage_number} command completed successfully.")
    else:
        st.error(f"Stage {stage_number} command failed with exit code {result['returncode']}.")

    st.caption(result.get("audit_status", ""))

    if result.get("audit_refresh_warning"):
        st.warning(result["audit_refresh_warning"])

    combined = result["stdout"]
    if result["stderr"]:
        combined += "\n\nSTDERR\n" + result["stderr"]

    with st.expander("Stage run console output", expanded=True):
        st.code(combined.strip() or "(command returned no console output)", language="text")


def _render_html_report_button(prefix: str) -> None:
    if not HTML_REPORT_PATH.exists():
        st.warning("reports/operations_report.html is not available yet.")
        return

    state_key = f"{prefix}_show_html"
    is_open = st.session_state.get(state_key, False)

    button_label = (
        "Hide HTML operations report"
        if is_open
        else "Open HTML operations report"
    )

    if st.button(button_label, key=f"{prefix}_html_button"):
        st.session_state[state_key] = not is_open
        is_open = not is_open

    if is_open:
        st.caption(
            "Generated business report. Stage run console output stays collapsed above."
        )
        components.html(
            HTML_REPORT_PATH.read_text(encoding="utf-8"),
            height=900,
            scrolling=True,
        )


def _render_overview(stage_number: int) -> None:
    stage = STAGES[stage_number]

    st.subheader("Aim")
    st.write(stage["aim"])

    st.subheader("Pipeline position")
    st.plotly_chart(
        _pipeline_figure(stage_number),
        use_container_width=True,
        config={"displayModeBar": False},
    )

    if stage["objects"]:
        st.subheader("Database objects")
        st.write(" • ".join(f"`{name}`" for name in stage["objects"]))

    if stage_number == 1:
        st.subheader("Source directory")
        st.text_input(
            "CSV / JSONL source location",
            value=str(PROJECT_ROOT / "data"),
            key="source_directory",
            help=(
                "You may select the repository root or its data/ directory. "
                "The explorer compares source files with DATASET_MANIFEST.json."
            ),
        )

        if st.button("Run Stage 1 validation", type="primary", key="run_stage_1"):
            _run_stage(1)

        if "stage_1_validation_message" in st.session_state:
            if st.session_state["stage_1_validation_success"]:
                st.success(st.session_state["stage_1_validation_message"])
            else:
                st.error(st.session_state["stage_1_validation_message"])

            frame = st.session_state["stage_1_validation"]
            if not frame.empty:
                st.dataframe(frame, use_container_width=True, hide_index=True)
                st.caption(f"Files checked: {len(frame)}")
        return

    if stage_number == 12:
        st.info(
            "Stage 12 is a documentation/design stage and does not mutate DuckDB."
        )
        if PRODUCTION_DESIGN_PATH.exists():
            with st.expander("View production design", expanded=True):
                st.markdown(PRODUCTION_DESIGN_PATH.read_text(encoding="utf-8"))
        return

    button_label = (
        "Run Stage 11 acceptance check"
        if stage_number == 11
        else f"Run Stage {stage_number}"
    )

    if st.button(button_label, type="primary", key=f"run_stage_{stage_number}"):
        with st.spinner(f"Running Stage {stage_number}..."):
            _run_stage(stage_number)

    if stage_number == 11:
        if st.button("Run full pytest regression suite", key="run_full_pytest"):
            with st.spinner("Running pytest..."):
                before = _audit_mtime()
                result = _run_module("pytest", timeout_seconds=1200)
                # `python -m pytest` needs special handling because _run_module would
                # run `python -m pytest`, which is exactly what is wanted.
                result["audit_status"] = _report_status(before)
                st.session_state["stage_run_11_pytest"] = result

        if "stage_run_11_pytest" in st.session_state:
            result = st.session_state["stage_run_11_pytest"]
            if result["returncode"] == 0:
                st.success("Full pytest regression suite passed.")
            else:
                st.error("Full pytest regression suite failed.")
            with st.expander("pytest output", expanded=True):
                st.code(
                    (result["stdout"] + "\n" + result["stderr"]).strip(),
                    language="text",
                )

    _render_run_output(stage_number)

    st.subheader("Stage results")
    result_frame = _result_dataframe(stage_number)
    if result_frame is None:
        st.info("Run/build the required database layers to display stage results.")
    elif result_frame.shape[0] == 1:
        _render_metric_row(result_frame)
        with st.expander("Result row"):
            st.dataframe(result_frame, use_container_width=True, hide_index=True)
    else:
        st.dataframe(result_frame, use_container_width=True, hide_index=True)

    if stage_number in {10, 11}:
        st.subheader("Business report")
        _render_html_report_button(prefix=f"stage_{stage_number}")


def _render_tables(stage_number: int) -> None:
    objects = STAGES[stage_number]["objects"]

    if not objects:
        if stage_number == 1:
            st.info(
                "Stage 1 creates no DuckDB table. Its evidence is the source-file "
                "row-count and SHA256 validation shown in Overview."
            )
        elif stage_number == 11:
            st.info(
                "Stage 11 creates no business-data table. It validates the complete "
                "existing model through acceptance checks and CI."
            )
        elif stage_number == 12:
            st.info(
                "Stage 12 creates no DuckDB table. It documents production-scale design."
            )
        return

    selector_col, editor_col = st.columns([0.27, 0.73], gap="large")

    with selector_col:
        st.markdown("#### Objects")
        selected_object = st.radio(
            "Select table/view",
            objects,
            key=f"object_selector_{stage_number}",
            label_visibility="collapsed",
        )

    with editor_col:
        st.markdown(f"#### `{selected_object}`")

        with st.expander("Creation SQL", expanded=False):
            st.code(_extract_ddl(selected_object), language="sql")

        default_query = f'SELECT *\nFROM "{selected_object}"\nLIMIT 10;'

        sql = st.text_area(
            "Editable table query",
            value=default_query,
            height=180,
            key=f"table_sql_{stage_number}_{selected_object}",
            help=(
                "The default returns 10 rows. Change columns, filters or values "
                "and run it again. The explorer accepts read-only SQL only."
            ),
        )

        if st.button(
            "Run table query",
            type="primary",
            key=f"run_table_{stage_number}_{selected_object}",
        ):
            _render_query_result(
                sql,
                key=f"table_result_{stage_number}_{selected_object}",
            )


def _render_sql_explorer(stage_number: int) -> None:
    queries = SQL_EXPLORER_QUERIES.get(stage_number, {"Show tables": qe.SHOW_ALL_TABLES})

    choice = st.selectbox(
        "Example query",
        list(queries),
        key=f"sql_example_{stage_number}",
    )

    explanation = getattr(qe, "QUERY_RETURNS", {}).get(choice)
    if explanation:
        st.caption(explanation)

    sql = st.text_area(
        "Editable SQL",
        value=queries[choice].strip(),
        height=300,
        key=f"sql_editor_{stage_number}_{choice}",
        help=(
            "Change identifiers, fields, filters or values before running. "
            "Only read-only SQL is accepted."
        ),
    )

    if st.button("Run SQL", type="primary", key=f"run_sql_{stage_number}_{choice}"):
        _render_query_result(sql, key=f"sql_result_{stage_number}_{choice}")


def _extract_report_section(stage_number: int) -> str | None:
    if not AUDIT_REPORT_PATH.exists():
        return None

    content = AUDIT_REPORT_PATH.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        rf"(?ims)(^.*STAGE\s+{stage_number}\b.*?)(?=^.*STAGE\s+{stage_number + 1}\b|\Z)"
    )
    match = pattern.search(content)

    if match:
        return match.group(1).strip()

    return None


def _render_audit_evidence(stage_number: int) -> None:
    if stage_number == 1:
        st.subheader("Source integrity evidence")
        if "stage_1_validation" not in st.session_state:
            st.info("Run Stage 1 from Overview to generate source-integrity evidence.")
        else:
            st.dataframe(
                st.session_state["stage_1_validation"],
                use_container_width=True,
                hide_index=True,
            )
    else:
        queries = AUDIT_QUERIES.get(stage_number, {})

        if queries:
            choice = st.selectbox(
                "Audit query",
                list(queries),
                key=f"audit_query_{stage_number}",
            )

            sql = st.text_area(
                "Editable audit SQL",
                value=queries[choice].strip(),
                height=280,
                key=f"audit_sql_{stage_number}_{choice}",
                help=(
                    "The query starts with a known audit example. Change IDs, fields "
                    "or filters to trace another record."
                ),
            )

            if st.button(
                "Run audit query",
                type="primary",
                key=f"run_audit_{stage_number}_{choice}",
            ):
                _render_query_result(
                    sql,
                    key=f"audit_result_{stage_number}_{choice}",
                )
        elif stage_number == 12:
            st.info(
                "Stage 12 is design documentation. Its evidence is "
                "docs/PRODUCTION_DESIGN.md rather than an audit query."
            )

    st.divider()
    st.subheader("Cumulative Report.txt evidence")

    if not AUDIT_REPORT_PATH.exists():
        st.warning("reports/Report.txt is not available yet.")
        return

    section = _extract_report_section(stage_number)

    with st.expander(
        f"Report.txt evidence for Stage {stage_number}",
        expanded=False,
    ):
        if section:
            st.code(section, language="text")
        else:
            st.info(
                "A dedicated Stage section was not found. "
                "The complete cumulative audit report is available below."
            )
            st.code(
                AUDIT_REPORT_PATH.read_text(encoding="utf-8", errors="replace")[-12000:],
                language="text",
            )

    st.caption("Audit evidence file: reports/Report.txt")


def _sidebar() -> int:
    st.sidebar.title("Pipeline Explorer")
    st.sidebar.caption("Stage navigation")

    labels = [
        f"{number}. {STAGES[number]['name']}"
        for number in STAGES
    ]

    selected_label = st.sidebar.radio(
        "Pipeline stages",
        labels,
        index=0,
        key="selected_stage_label",
        label_visibility="collapsed",
    )

    selected_stage = int(selected_label.split(".", 1)[0])

    st.sidebar.divider()
    st.sidebar.caption("Database")
    st.sidebar.code("data/modelled/onside.duckdb", language="text")
    st.sidebar.caption("Engineering audit")
    st.sidebar.code("reports/Report.txt", language="text")
    st.sidebar.caption("Business report")
    st.sidebar.code("reports/operations_report.html", language="text")

    return selected_stage


def main() -> None:
    _set_page()
    selected_stage = _sidebar()
    _stage_header(selected_stage)

    # Separate right-side workspace layer. Only one workspace is rendered at once,
    # keeping the page uncluttered.
    workspace = st.radio(
        "Workspace",
        ["Overview", "Tables", "SQL Explorer", "Audit Evidence"],
        horizontal=True,
        key=f"workspace_{selected_stage}",
        label_visibility="collapsed",
    )

    st.divider()

    if workspace == "Overview":
        _render_overview(selected_stage)
    elif workspace == "Tables":
        _render_tables(selected_stage)
    elif workspace == "SQL Explorer":
        _render_sql_explorer(selected_stage)
    else:
        _render_audit_evidence(selected_stage)


# =============================================================================
# ENHANCED REVIEWER UI (v2)
# =============================================================================
# The definitions below intentionally override the earlier lightweight UI helpers.
# Core pipeline modules and query_examples remain the authoritative business logic.


STAGE_DETAILS: dict[int, dict[str, Any]] = {
    1: {
        "aim": (
            "Protect the raw evidence before any transformation begins. This stage checks every supplied CSV and JSONL file against DATASET_MANIFEST.json using physical row counts and SHA256 hashes. JSONL is counted as physical lines rather than parsed here because malformed JSON is intentionally handled later. The result is a trusted byte-for-byte source baseline for every downstream calculation."
        ),
        "files": [
            ("src/verify_sources.py", "Counts CSV/JSONL rows and verifies SHA256 checksums without parsing malformed JSONL."),
        ],
        "test_path": None,
        "stage_tests": None,
        "cumulative_tests": 0,
    },
    2: {
        "aim": (
            "Load the six CSV reference sources into typed DuckDB tables inside one transaction. The loader validates expected counts, stable keys/relationships, incident severity values and report-week structure. These tables provide the clean reference entities used by later joins and analytical models."
        ),
        "files": [
            ("src/database.py", "Central DuckDB connection/schema setup with deterministic UTC/Gregorian session settings."),
            ("src/load_reference_data.py", "Loads and validates all six CSV reference sources atomically."),
            ("tests/test_reference_data.py", "Checks counts, relationships, domains and report-week rules."),
        ],
        "test_path": "tests/test_reference_data.py",
        "stage_tests": 11,
        "cumulative_tests": 11,
    },
    3: {
        "aim": (
            "Land the six visit-change JSONL batches in manifest order while preserving every physical line before parsing. ingestion_batches stores batch identity, source hash, status, attempts and counters; raw_visit_lines stores the exact raw payload and line SHA256. Replay of an already landed/completed batch is a no-op, and failed landing rolls back partial raw rows so restart is safe."
        ),
        "files": [
            ("src/land_visit_batches.py", "Reads manifest order, verifies batch identity/content, lands raw lines transactionally and supports replay/restart."),
            ("tests/test_batch_landing.py", "Checks order, counts, replay no-op and simulated failure/restart."),
        ],
        "test_path": "tests/test_batch_landing.py",
        "stage_tests": 6,
        "cumulative_tests": 17,
    },
    4: {
        "aim": (
            "Separate technically processable visit-change deliveries from malformed/incomplete source lines. Checks include: valid JSON object; non-empty event_id and visit_id; positive integer source_version; operation in {upsert, delete}; timezone-aware updated_at; complete upserts with property_id, contractor_id, visitor_id and started_at; ended_at key present but allowed to be null; and the expected delete shape. Invalid lines are retained with batch, line, reason codes and raw evidence. Unknown business references and end-before-start are deliberately deferred to Stage 6."
        ),
        "files": [
            ("src/validate_visit_events.py", "Parses landed lines, applies technical rules and records reject evidence."),
            ("tests/test_visit_validation.py", "Checks malformed JSON, incomplete upserts, timestamps, replay and validation boundaries."),
        ],
        "test_path": "tests/test_visit_validation.py",
        "stage_tests": 12,
        "cumulative_tests": 29,
    },
    5: {
        "aim": (
            "Build deterministic visit state from technically valid deliveries. event_id is the global deduplication key, so repeated physical deliveries become duplicate evidence instead of being applied twice. Every unique event is kept in visit_events. For each visit_id, only a strictly higher source_version can change current state; older/equal versions are stale. Delete events are retained as tombstones so late older upserts cannot resurrect a visit, while a genuinely newer upsert can reactivate it."
        ),
        "files": [
            ("src/process_visit_state.py", "Deduplicates event deliveries and applies per-visit source_version precedence/tombstones."),
            ("tests/test_visit_state.py", "Checks duplicates, stale versions, delete tombstones, corrections and reactivation."),
        ],
        "test_path": "tests/test_visit_state.py",
        "stage_tests": 11,
        "cumulative_tests": 40,
    },
    6: {
        "aim": (
            "Evaluate the latest active visit state for business reporting after technical processing is complete. This stage flags unknown property references, unknown contractor references and completed visits whose ended_at precedes started_at. Deleted visits are not quality failures, and unfinished visits are valid. Problem records remain auditable while reportable_current_visits exposes only clean active visits for analytics."
        ),
        "files": [
            ("src/check_visit_quality.py", "Evaluates active current visits against business-quality rules and rebuilds the reportable view."),
            ("tests/test_visit_quality.py", "Checks unknown references, negative duration, deleted visits and unfinished visits."),
        ],
        "test_path": "tests/test_visit_quality.py",
        "stage_tests": 10,
        "cumulative_tests": 50,
    },
    7: {
        "aim": (
            "Reconstruct which company managed each property at any business timestamp. Consecutive unchanged observations collapse into one interval, a later return to a previous company creates a new interval, and intervals use [start, end) semantics so activity exactly at a transfer belongs to the new company. A separate current-management view keeps today's manager distinct from historical attribution."
        ),
        "files": [
            ("src/build_management_history.py", "Transforms management observations into effective-dated intervals and current-management lookup."),
            ("tests/test_management_history.py", "Checks interval collapse, return-to-prior-manager behaviour and exact transfer boundaries."),
        ],
        "test_path": "tests/test_management_history.py",
        "stage_tests": 10,
        "cumulative_tests": 60,
    },
    8: {
        "aim": (
            "Create analytics-ready visit and incident facts without joining their grains together. Visits are attributed by started_at and incidents by reported_at to the historical manager. Reporting weeks are Monday-based in each property's IANA timezone and are marked requested only when week_start_local appears in src_report_weeks. Completed visit hours use elapsed UTC/TIMESTAMPTZ time so daylight-saving changes do not distort duration. Unfinished visits count as visits/distinct visitors but contribute no completed hours. Incident severity remains the source low/medium/high value, with is_high derived as true exactly for severity='high'."
        ),
        "files": [
            ("src/build_analytical_facts.py", "Builds historically attributed, timezone-aware visit and incident facts."),
            ("tests/test_analytical_facts.py", "Checks historical attribution, local-week boundaries, DST duration, unfinished visits and period filtering."),
        ],
        "test_path": "tests/test_analytical_facts.py",
        "stage_tests": 11,
        "cumulative_tests": 71,
    },
    9: {
        "aim": (
            "Aggregate the fact layers into report-ready grains while preserving metric semantics. Visit and incident measures are aggregated separately before combination, distinct visitors are recalculated at each displayed grain, and a property × requested-week scaffold keeps zero-activity rows visible. The result is stable company/week, company-period, property/week and contractor marts."
        ),
        "files": [
            ("src/build_reporting_marts.py", "Builds safe reporting grains from separate visit and incident facts."),
            ("tests/test_reporting_marts.py", "Checks mart counts, zero-activity scaffold and non-additive distinct visitors."),
        ],
        "test_path": "tests/test_reporting_marts.py",
        "stage_tests": 12,
        "cumulative_tests": 83,
    },
    10: {
        "aim": (
            "Turn the reporting marts into a reviewer/business-facing artifact. This stage generates the self-contained HTML operations report and CSV extracts, records report-build metadata, presents headline KPIs, weekly company trends, property summaries, contractor activity and data-quality/load evidence, and documents definitions/limitations. The explorer also exposes editable company/date-range and contractor/date-range visit drill-downs so report totals can be traced back to visitors, contractors, properties, historical companies, completion status and known completed hours."
        ),
        "files": [
            ("src/generate_report.py", "Renders the Plotly/Jinja2 HTML report, CSV extracts and report-build metadata."),
            ("src/pipeline_presenter.py", "Provides stage explanations/results used by the terminal walkthrough."),
            ("src/run_pipeline.py", "Orchestrates the complete interactive/non-interactive pipeline."),
            ("tests/test_report.py", "Checks report generation, extract counts, metadata and required content."),
        ],
        "test_path": "tests/test_report.py",
        "stage_tests": 8,
        "cumulative_tests": 91,
    },
    11: {
        "aim": (
            "Prove that the repository is reproducible outside the developer's laptop. The acceptance gate checks cross-layer counts and final artifacts. GitHub Actions performs source verification, a clean non-interactive rebuild, acceptance checks, the full pytest suite, raw-data immutability verification and report artifact upload on a clean Linux/Python 3.11 runner."
        ),
        "files": [
            ("src/acceptance_check.py", "Final cross-layer gate for counts, latest batch and generated artifacts."),
            ("tests/test_ci_reproducibility.py", "Checks CI workflow/configuration and stable acceptance expectations."),
        ],
        "test_path": "tests/test_ci_reproducibility.py",
        "stage_tests": 5,
        "cumulative_tests": 96,
    },
    12: {
        "aim": (
            "Document how the same correctness rules could evolve for datasets with millions of records. The design maps immutable raw landing to S3, buffering/routing to SQS/EventBridge where justified, lightweight orchestration to Lambda, incremental analytical storage to Snowflake, and adds security, monitoring, CI/CD, replay, schema/versioning and rollback considerations. This is a proposed reference architecture, not a claim about Onside's production stack."
        ),
        "files": [],
        "test_path": None,
        "stage_tests": None,
        "cumulative_tests": 96,
    },
}

for _stage_number, _detail in STAGE_DETAILS.items():
    STAGES[_stage_number].update(_detail)


OBJECT_INFO: dict[str, tuple[str, str, str]] = {
    "src_companies": ("One managing company.", "Stable company reference for management/reporting joins.", "3 source rows."),
    "src_contractors": ("One contractor.", "Stable contractor reference for visit enrichment and contractor reporting.", "8 source rows."),
    "src_properties": ("One property.", "Property reference including IANA timezone used for local-week calculation.", "24 source rows."),
    "src_property_management": ("One management observation.", "Raw effective-at observations used to reconstruct historical management.", "62 observations."),
    "src_incidents": ("One incident.", "Independent incident source fact kept separate from visits.", "247 source rows."),
    "src_report_weeks": ("One requested Monday week.", "Defines the six reporting weeks included in requested-period metrics.", "6 rows."),
    "ingestion_batches": ("One manifest batch.", "Durable batch control/checkpoint metadata for order, replay, attempts and counts.", "6 batches."),
    "raw_visit_lines": ("One physical JSONL line.", "Preserves exact raw payload and SHA256 before parsing.", "1,997 rows."),
    "validated_visit_events": ("One technically valid physical delivery.", "Typed/parsed delivery layer after Stage 4 technical validation.", "1,979 rows."),
    "visit_event_rejects": ("One technically invalid physical line.", "Retains reject reason and raw evidence instead of silently dropping bad data.", "18 rows."),
    "visit_events": ("One globally unique event_id.", "Logical event history with applied/stale state decision.", "1,944 unique events."),
    "visit_event_duplicates": ("One repeated physical delivery after the canonical first occurrence.", "Stage 5 checks each validated event_id against visit_events. The first occurrence becomes the canonical event; later identical deliveries are written here, with canonical_batch_id/canonical_line_number pointing back to the first occurrence, and are not applied to visit state again.", "35 repeated physical deliveries."),
    "visit_current_state": ("One visit_id, regardless of how many historical events that visit has.", "Stores the current winning state for each visit: the highest applied source_version, including delete tombstones. Historical/stale events remain in visit_events instead of becoming extra current visits.", "1,618 current visit states; 1,567 active and 51 deleted tombstones."),
    "visit_quality_issues": ("One visit/rule issue.", "Preserves business-quality evidence for unknown references/end-before-start.", "3 affected visits."),
    "reportable_current_visits": ("One clean active visit.", "View excluding tombstones and visits with business-quality issues.", "1,564 rows."),
    "property_management_intervals": ("One contiguous manager interval per property.", "Historical [valid_from, valid_to) attribution layer.", "37 intervals."),
    "current_property_management": ("One current manager per property.", "Convenient current lookup kept separate from historical attribution.", "24 rows."),
    "fact_visits": ("One reportable current visit.", "Historical company, local week, contractor and DST-safe duration at visit grain.", "1,564 rows."),
    "fact_incidents": ("One incident.", "Historical company and local week at incident grain.", "247 rows."),
    "rpt_company_week": ("One company × requested week.", "Weekly company trend metrics without fact fanout.", "18 rows."),
    "rpt_company_period": ("One company for requested period.", "Company totals with distinct visitors calculated at company grain.", "3 rows."),
    "rpt_property_week": ("One property × requested week.", "Scaffold keeps zero-activity property/weeks visible.", "144 rows; 22 zero-activity."),
    "rpt_contractor": ("One contractor for requested period.", "Contractor visit-activity summary.", "8 rows."),
    "report_builds": ("One named report build.", "Records generated-at time, output paths, latest batch and reporting period.", "1 current build row."),
}


def _q(sql: str, purpose: str, result: str, kind: str = "Inspection") -> dict[str, str]:
    return {"sql": sql, "purpose": purpose, "result": result, "kind": kind}


STAGE_QUERIES: dict[int, dict[str, dict[str, str]]] = {
    2: {
        "Reference source reconciliation": _q(
            qe.STAGE_2_REFERENCE_COUNTS,
            "Reconcile the six typed reference tables with the supplied CSV sources.",
            "Returns one row per reference table and its loaded row count.",
            "Audit / reconciliation",
        ),
    },
    3: {
        "Batch loading status": _q(
            qe.STAGE_3_BATCH_COUNTS,
            "Check manifest order, physical line counts, status and retry attempts for every batch.",
            "Returns one row per ingestion batch.",
            "Audit / reconciliation",
        ),
        "Trace one raw source line": _q(
            qe.STAGE_3_RAW_LINE,
            "Trace one physical JSONL source line into raw_visit_lines before parsing.",
            "Returns source batch/line, raw payload and its SHA256 fingerprint.",
        ),
    },
    4: {
        "Technical rejects with raw source record": _q(
            qe.STAGE_4_REJECTS,
            "Show each Stage 4 reject together with its source location, extracted event ID, reject category, reason and the exact raw source record.",
            "Returns one merged evidence row per reject, so the reviewer does not need to cross-reference a second raw-evidence table.",
            "Audit / validation",
        ),
        "Trace a technically valid event": _q(
            qe.STAGE_4_VALID_EVENT,
            "Trace E00275 after JSON parsing, required-field checks and timestamp/type validation.",
            "Returns the typed validated delivery.",
        ),
    },
    5: {
        "Discover duplicate event_id deliveries": _q(
            qe.STAGE_5_DUPLICATE_DISCOVERY,
            "Derive duplicate deliveries directly from validated_visit_events. The first physical occurrence of each event_id is canonical; later identical deliveries are duplicates.",
            "Labels each repeated physical delivery CANONICAL or DUPLICATE and shows the file/line that feeds visit_event_duplicates.",
            "Audit / duplicate derivation",
        ),
        "Deleted visit tombstones": _q(
            qe.STAGE_5_DELETED_TOMBSTONES,
            "Inspect latest delete states retained in visit_current_state so stale older upserts cannot resurrect deleted visits.",
            "Returns all 51 deleted current states and their winning event/source location.",
            "Audit / delete retention",
        ),
        "V02 version and tombstone history": _q(
            qe.STAGE_5_V02_EVENT_HISTORY,
            "Demonstrate source_version precedence in true processing order: manifest batch order first, then physical line number within the batch.",
            "Returns V02 events in arrival/processing order, showing why the late version-1 event has prior_source_version=2 and becomes stale.",
            "Audit / version trace",
        ),
        "SYN_V00256 correction history (processing order)": _q(
            qe.STAGE_5_CORRECTION_HISTORY,
            "Trace a genuine higher-version correction.",
            "Returns both versions and their state actions.",
            "Audit / correction",
        ),
        "E00680 incoming vs existing state": _q(
            qe.STAGE_5_E00680_INCOMING_VS_EXISTING,
            "Reconstruct the exact current applied state for the same visit immediately before stale event E00680 was processed.",
            "Shows incoming/existing event IDs, versions, file/line and the precedence decision.",
            "Audit / precedence proof",
        ),
        "E00680 equal-version field comparison": _q(
            qe.STAGE_5_E00680_FIELD_COMPARISON,
            "Verify the equal-version incoming event matches the existing state fields checked by Stage 5.",
            "Returns side-by-side values and boolean equality guards.",
            "Audit / equality guard",
        ),
    },
    6: {
        "Business quality issues": _q(
            qe.STAGE_6_QUALITY_ISSUES,
            "Show technically valid current visits that fail business-quality rules.",
            "Returns V11/V12/V13 with issue code and evidence.",
            "Audit / quality",
        ),
        "Active / invalid / reportable reconciliation": _q(
            qe.STAGE_6_COUNTS,
            "Reconcile active current visits with business-invalid and final reportable counts.",
            "Returns active, affected-quality and reportable counts.",
            "Audit / reconciliation",
        ),
    },
    7: {
        "P03 management intervals": _q(
            qe.STAGE_7_P03_INTERVALS,
            "Inspect the reconstructed effective-dated history for P03.",
            "Returns P03 [valid_from, valid_to) intervals, start observation and collapsed-observation count.",
        ),
        "P03 boundary interval-join proof": _q(
            qe.STAGE_7_P03_BOUNDARY_JOIN_PROOF,
            "Prove the 12:59/13:00 transfer using the interval join itself rather than relying on Stage 8 facts.",
            "Shows each activity record, matched company interval and boolean [start,end) conditions.",
            "Audit / boundary calculation",
        ),
        "Property management history: exact time or range": _q(
            qe.STAGE_7_PROPERTY_AT_TIMESTAMP,
            "Given a property, use lookup_mode='exact' to find the manager at one UTC timestamp, or lookup_mode='range' to return every management interval overlapping a UTC time range.",
            "Edit property_id, lookup_mode and the timestamp/range. The result shows the historical company, full management interval and the portion matched by the requested time.",
            "Lookup / editable",
        ),
        "Company properties in time range": _q(
            qe.STAGE_7_COMPANY_PROPERTIES_IN_RANGE,
            "Given a company and [from,to) UTC range, find properties it managed during any overlapping portion of that range.",
            "Edit company_id/from_time/to_time and receive overlapping historical intervals.",
            "Lookup / editable",
        ),
    },
    8: {
        "Historical attribution by property/time range": _q(
            qe.STAGE_8_HISTORICAL_ATTRIBUTION_BY_PROPERTY_RANGE,
            "Filter fact visits and incidents by an editable property/time range and inspect the historical company stored on each fact.",
            "Defaults to P11 around its C03→C01 change; edit property_id/from_time/to_time instead of hard-coded visit IDs.",
            "Audit / historical attribution",
        ),
        "Requested report weeks": _q(
            qe.STAGE_8_REQUESTED_REPORT_WEEKS,
            "Show the local Monday week labels explicitly requested by report_weeks.csv.",
            "Returns each requested week_start and its local Sunday week end.",
            "Audit / period definition",
        ),
        "Requested-period exclusion summary": _q(
            qe.STAGE_8_PERIOD_EXCLUSION_SUMMARY,
            "Reconcile valid facts that remain in the fact tables but are outside the requested local weeks.",
            "Returns 21 valid visits and 1 valid incident outside the report period.",
            "Audit / period exclusion",
        ),
        "Outside-period incident explanation": _q(
            qe.STAGE_8_OUTSIDE_INCIDENT_EXPLANATION,
            "Inspect the valid incident whose property-local Monday week is not present in report_weeks.csv.",
            "Shows UTC/local timestamp, derived local week, severity, high flag and requested-week flag.",
            "Audit / period exclusion",
        ),
        "Incident severity summary": _q(
            qe.STAGE_8_INCIDENT_SEVERITY_SUMMARY,
            "Explain the source severity categories and the derived is_high flag.",
            "Groups low/medium/high incidents and shows that is_high is true only for severity='high'.",
            "Audit / severity",
        ),
        "Local week boundary": _q(
            qe.STAGE_8_LOCAL_WEEK_BOUNDARY,
            "Show that one UTC minute can cross a property-local Monday reporting-week boundary.",
            "Returns V14/V01 UTC/local timestamps and different local week labels.",
            "Audit / timezone",
        ),
        "All UTC/local week shifts": _q(
            qe.STAGE_8_ALL_LOCAL_WEEK_SHIFTS,
            "Find every visit whose correct property-local Monday week differs from the UTC week label.",
            "Returns all boundary-sensitive visit facts.",
            "Audit / timezone",
        ),
        "DST-safe duration": _q(
            qe.STAGE_8_DST_DURATION,
            "Show why completed duration uses timestamp instants rather than local wall-clock subtraction.",
            "Returns DST examples where local clock labels appear one hour apart but actual elapsed duration is two hours.",
            "Audit / DST",
        ),
        "Visits by historical company/time range": _q(
            qe.STAGE_8_VISITS_BY_HISTORICAL_COMPANY_RANGE,
            "Choose a historically attributed company name and UTC [from,to) range, then inspect the individual visit facts managed by that company when each visit started.",
            "Returns property, visit, visitor, contractor, start/end time, completed/unfinished status and completed hours. Edit company_name/from_time/to_time.",
            "Analysis / historical company",
        ),
        "Visits by contractor/time range": _q(
            qe.STAGE_8_VISITS_BY_CONTRACTOR_RANGE,
            "Choose a contractor name and UTC [from,to) range to inspect every matching visit fact.",
            "Returns visit, visitor, historical managing company, property, start/end time, completed/unfinished status and completed hours. Edit contractor_name/from_time/to_time.",
            "Analysis / contractor",
        ),
        "Visit status by company/property": _q(
            qe.STAGE_8_VISIT_STATUS_BY_COMPANY_PROPERTY,
            "Summarize completed versus unfinished visits and completed hours using editable company/property names.",
            "Returns visit counts, completed count, unfinished count and known completed hours. Unfinished duration is intentionally unknown.",
            "Analysis / editable",
        ),
        "Visits by local date range": _q(
            qe.STAGE_8_VISITS_BY_LOCAL_DATE_RANGE,
            "Filter visit facts by property-local calendar date; use the same from/to date for a single day.",
            "Returns detailed visit facts for the inclusive local-date range.",
            "Analysis / date filter",
        ),
        "Incidents by local date range": _q(
            qe.STAGE_8_INCIDENTS_BY_LOCAL_DATE_RANGE,
            "Filter incident facts by property-local calendar date; use the same from/to date for a single day.",
            "Returns detailed incident facts for the inclusive local-date range.",
            "Analysis / date filter",
        ),
        "Headline KPI facts": _q(
            qe.STAGE_8_REPORT_KPIS,
            "Recalculate the six requested-period headline metrics directly from facts.",
            "Returns visits, distinct visitors, completed hours, unfinished visits, incidents and high incidents.",
            "Audit / metrics",
        ),
    },
    9: {
        "Company period mart": _q(qe.STAGE_9_COMPANY_PERIOD, "Inspect company totals feeding the report.", "Returns one requested-period row per company."),
        "Zero-activity property weeks": _q(qe.STAGE_9_ZERO_ACTIVITY, "Prove scaffolded property/week rows remain visible even with no activity.", "Returns all zero-activity property/week rows.", "Audit / completeness"),
        "Distinct visitor non-additivity": _q(qe.STAGE_9_DISTINCT_NON_ADDITIVE, "Show why lower-grain distinct visitor counts cannot be added together.", "Returns true overall distinct visitors versus summed company distincts.", "Audit / metric semantics"),
        "Contractor mart": _q(qe.STAGE_9_CONTRACTORS, "Inspect contractor metrics used by the report.", "Returns contractor-period metrics ordered by visits."),
    },
    10: {
        "Company visit drilldown by UTC range": _q(
            qe.STAGE_10_COMPANY_VISIT_DRILLDOWN,
            "Given a company name and [from,to) UTC range, show the actual visits behind company report totals.",
            "Returns visitor, contractor, property, visit status and completed hours. Edit company_name/from_time/to_time.",
            "Business drilldown / editable",
        ),
        "Contractor visit drilldown by UTC range": _q(
            qe.STAGE_10_CONTRACTOR_VISIT_DRILLDOWN,
            "Given a contractor name and [from,to) UTC range, show where that contractor worked and which historical company managed each property.",
            "Returns visit, visitor, company, property, completed/unfinished status and completed hours. Incidents are intentionally absent because the source has no incident-to-contractor key.",
            "Business drilldown / editable",
        ),
        "Headline KPI facts": _q(qe.STAGE_8_REPORT_KPIS, "Trace HTML headline values back to fact aggregation.", "Returns the six KPI values.", "Audit / metrics"),
        "Company period mart": _q(qe.STAGE_9_COMPANY_PERIOD, "Trace the company summary displayed in the HTML report.", "Returns the three company-period rows."),
        "Report build metadata": _q(qe.STAGE_10_REPORT_BUILD, "Confirm which report build/latest batch/reporting period were generated.", "Returns report-build metadata.", "Audit / report"),
    },
    11: {
        "Headline KPI facts": _q(qe.STAGE_8_REPORT_KPIS, "Confirm final report metrics after reproducible build.", "Returns the six final KPI values.", "Audit / acceptance"),
        "Report build metadata": _q(qe.STAGE_10_REPORT_BUILD, "Confirm the expected report build exists.", "Returns report metadata.", "Audit / acceptance"),
    },
    12: {
        "Show final model": _q(qe.SHOW_ALL_TABLES, "Inspect the local model that the production design proposes to evolve.", "Returns all DuckDB tables/views."),
        "Describe current visit state": _q(qe.DESCRIBE_VISIT_CURRENT_STATE, "Inspect state semantics preserved at production scale.", "Returns columns, types and key metadata."),
        "Describe visit facts": _q(qe.DESCRIBE_FACT_VISITS, "Inspect the analytical visit grain that maps to a warehouse fact model.", "Returns columns, types and key metadata."),
    },
}


def _set_page() -> None:
    st.set_page_config(
        page_title="Property Operations Pipeline Explorer",
        page_icon="🧭",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
<style>
.block-container { padding-top: 1.0rem; padding-bottom: 2rem; }
[data-testid="stSidebar"] { min-width: 330px; max-width: 330px; }
[data-testid="stSidebar"] .stRadio label { line-height: 1.25rem; }
.small-note { font-size: 0.88rem; opacity: 0.75; }
</style>
""",
        unsafe_allow_html=True,
    )


def _pipeline_figure(selected_stage: int) -> go.Figure:
    labels = [
        "Raw\nsources", "Typed\nreference", "Raw\nlanding", "Technical\nvalidation",
        "Versioned\nstate", "Business\nquality", "Management\nhistory", "Facts",
        "Marts", "HTML\nreport", "CI", "Production\ndesign",
    ]
    x = list(range(1, 13))
    colors = ["#ff4b4b" if i == selected_stage else "#cbd5e1" for i in x]
    sizes = [30 if i == selected_stage else 19 for i in x]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=[0] * 12, mode="lines",
        line={"width": 2, "color": "#94a3b8"},
        hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=x, y=[0] * 12, mode="markers+text",
        marker={"size": sizes, "color": colors, "line": {"color": "#ffffff", "width": 1.5}},
        text=labels, textposition="bottom center", customdata=x,
        hovertemplate="Stage %{customdata}<extra></extra>", showlegend=False,
    ))
    fig.update_layout(
        height=175,
        margin={"l": 10, "r": 10, "t": 5, "b": 50},
        xaxis={"visible": False, "range": [0.4, 12.6]},
        yaxis={"visible": False, "range": [-1, 1]},
    )
    return fig


def _stage_header(stage_number: int) -> None:
    stage = STAGES[stage_number]
    st.title(f"Stage {stage_number} — {stage['name']}")
    st.caption("Property Operations Pipeline Explorer")
    st.plotly_chart(
        _pipeline_figure(stage_number),
        use_container_width=True,
        config={"displayModeBar": False},
    )


def _sidebar() -> int:
    st.sidebar.title("Pipeline Explorer")
    st.sidebar.caption("Stage navigation")
    labels = [f"{n}. {STAGES[n]['name']}" for n in STAGES]
    selected = st.sidebar.radio(
        "Pipeline stages", labels, index=0,
        key="selected_stage_label", label_visibility="collapsed",
    )
    stage_number = int(selected.split(".", 1)[0])

    st.sidebar.divider()
    st.sidebar.caption("Database")
    st.sidebar.code("data/modelled/onside.duckdb", language="text")
    st.sidebar.caption("Engineering audit")
    st.sidebar.code("reports/Report.txt", language="text")
    st.sidebar.caption("Business report")
    st.sidebar.code("reports/operations_report.html", language="text")
    return stage_number


def _choose_project_folder(initial_dir: str) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        selected = filedialog.askdirectory(
            title="Select the project repository root",
            initialdir=initial_dir if Path(initial_dir).exists() else str(PROJECT_ROOT),
        )
        root.destroy()
        return selected or None
    except Exception:
        return None


def _browse_project_folder_callback() -> None:
    current = st.session_state.get("project_root_input", str(PROJECT_ROOT))
    selected = _choose_project_folder(current)
    if selected:
        st.session_state["project_root_input"] = selected
        st.session_state["folder_dialog_error"] = ""
    else:
        st.session_state["folder_dialog_error"] = (
            "The native folder browser was not available. Paste the repository root path manually."
        )


def _count_csv_rows_local(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for _ in reader)


def _count_jsonl_rows_local(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _count_source_rows_local(path: Path) -> int:
    if path.suffix.lower() == ".csv":
        return _count_csv_rows_local(path)
    if path.suffix.lower() == ".jsonl":
        return _count_jsonl_rows_local(path)
    raise ValueError(f"Unsupported source type: {path.suffix}")


def _sha256_local(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_project_root(project_path: str) -> tuple[pd.DataFrame, bool, str]:
    root = Path(project_path).expanduser()
    if not root.exists() or not root.is_dir():
        return pd.DataFrame(), False, f"I can't open the selected project directory: {root}"

    dataset_manifest = root / "DATASET_MANIFEST.json"
    batch_manifest = root / "manifest.json"
    data_dir = root / "data"
    missing = [p.name for p in (dataset_manifest, batch_manifest, data_dir) if not p.exists()]
    if missing:
        return (
            pd.DataFrame(), False,
            "This does not look like the project root. Missing: " + ", ".join(missing),
        )

    with dataset_manifest.open("r", encoding="utf-8") as handle:
        expected_files: dict[str, dict[str, object]] = json.load(handle)

    rows: list[dict[str, object]] = []
    all_ok = True
    for relative_path, expected in expected_files.items():
        source = root / relative_path
        if not source.exists():
            rows.append({
                "source_file": relative_path,
                "actual_rows": None,
                "expected_rows": int(expected["rows"]),
                "row_check": "CAN'T OPEN",
                "sha256_check": "CAN'T OPEN",
                "status": "FAIL",
            })
            all_ok = False
            continue
        try:
            actual_rows = _count_source_rows_local(source)
            actual_hash = _sha256_local(source)
        except OSError:
            rows.append({
                "source_file": relative_path,
                "actual_rows": None,
                "expected_rows": int(expected["rows"]),
                "row_check": "CAN'T OPEN",
                "sha256_check": "CAN'T OPEN",
                "status": "FAIL",
            })
            all_ok = False
            continue

        expected_rows = int(expected["rows"])
        expected_hash = str(expected["sha256"])
        rows_ok = actual_rows == expected_rows
        hash_ok = actual_hash == expected_hash
        rows.append({
            "source_file": relative_path,
            "actual_rows": actual_rows,
            "expected_rows": expected_rows,
            "row_check": "MATCH" if rows_ok else "MISMATCH",
            "sha256_check": "MATCH" if hash_ok else "MISMATCH",
            "status": "PASS" if rows_ok and hash_ok else "FAIL",
        })
        all_ok = all_ok and rows_ok and hash_ok

    message = (
        "All supplied source files match DATASET_MANIFEST.json."
        if all_ok
        else "One or more supplied files are missing, unreadable or do not match the manifest."
    )
    return pd.DataFrame(rows), all_ok, message


def _object_count(object_name: str) -> int | None:
    try:
        frame, _ = _database_query(f'SELECT COUNT(*) AS row_count FROM "{object_name}"')
        return int(frame.iloc[0]["row_count"])
    except Exception:
        return None


def _run_command(command: list[str], timeout_seconds: int = 1200) -> dict[str, Any]:
    started = datetime.now()
    try:
        completed = subprocess.run(
            command, cwd=PROJECT_ROOT, text=True, capture_output=True,
            timeout=timeout_seconds, check=False,
        )
        return {
            "command": " ".join(command), "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
            "started": started, "finished": datetime.now(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": " ".join(command), "returncode": -1,
            "stdout": exc.stdout or "", "stderr": f"Command exceeded {timeout_seconds} seconds.",
            "started": started, "finished": datetime.now(),
        }


def _run_module(module_name: str, timeout_seconds: int = 1200) -> dict[str, Any]:
    return _run_command([sys.executable, "-m", module_name], timeout_seconds)



def _passed_test_count(output: str) -> int | None:
    matches = re.findall(r"(\d+)\s+passed\b", output)
    return int(matches[-1]) if matches else None


def _report_has_stage(stage_number: int) -> bool:
    if not AUDIT_REPORT_PATH.exists():
        return False
    content = AUDIT_REPORT_PATH.read_text(encoding="utf-8", errors="replace")
    return bool(re.search(rf"(?im)^\s*STAGE\s+{stage_number}\b", content))


def _refresh_audit_report() -> dict[str, Any]:
    return _run_module("src.audit_report", timeout_seconds=120)


def _run_stage(stage_number: int) -> None:
    stage = STAGES[stage_number]

    if stage_number == 1:
        root_path = st.session_state.get("project_root_input", str(PROJECT_ROOT))
        frame, success, message = validate_project_root(root_path)
        st.session_state["stage_1_validation"] = frame
        st.session_state["stage_1_validation_success"] = success
        st.session_state["stage_1_validation_message"] = message
        st.session_state["stage_1_files_passed"] = (
            int((frame["status"] == "PASS").sum()) if not frame.empty else 0
        )
        return

    module_name = stage.get("module")
    if not module_name:
        return

    # Run only the stage command here. Stage tests are intentionally NOT rerun
    # from the explorer because their verified checkpoint counts are already
    # known and rerunning them makes the live walkthrough unnecessarily slow.
    result = _run_module(module_name)

    audit_result = None
    if result["returncode"] == 0 and 2 <= stage_number <= 10:
        audit_result = _refresh_audit_report()

    result["audit_result"] = audit_result
    result["report_has_current_stage"] = _report_has_stage(stage_number)
    st.session_state[f"stage_run_{stage_number}"] = result

    # Open the console output automatically immediately after a stage run so the
    # reviewer can see what the command produced without needing another click.
    st.session_state[f"stage_console_open_{stage_number}"] = True

    # Stage 10 creates the business-facing HTML report. Because the Pipeline
    # Explorer is intended to run locally, open the generated report in the
    # user's default browser as a new tab/window immediately after a successful
    # Stage 10 run. Keep the normal in-page report button as a manual fallback.
    if stage_number == 10 and result["returncode"] == 0:
        st.session_state["stage_10_show_html"] = False

        if HTML_REPORT_PATH.exists():
            try:
                opened = webbrowser.open_new_tab(
                    HTML_REPORT_PATH.resolve().as_uri()
                )
                result["report_auto_opened"] = bool(opened)
            except Exception as exc:
                result["report_auto_opened"] = False
                result["report_auto_open_error"] = str(exc)
        else:
            result["report_auto_opened"] = False
            result["report_auto_open_error"] = (
                "reports/operations_report.html was not found after Stage 10."
            )

        st.session_state[f"stage_run_{stage_number}"] = result


def _render_implementation_files(stage_number: int) -> None:
    st.subheader("Implementation files")
    files = STAGES[stage_number].get("files", [])
    if not files:
        if stage_number == 12:
            st.write("No Python transformation file is created in this documentation stage.")
            st.code("docs/PRODUCTION_DESIGN.md", language="text")
        else:
            st.write("No additional Python implementation file is required for this stage.")
        return
    st.dataframe(
        pd.DataFrame(files, columns=["Python file", "Responsibility"]),
        use_container_width=True, hide_index=True,
    )


def _render_stage_run_output(stage_number: int) -> None:
    key = f"stage_run_{stage_number}"
    if key not in st.session_state:
        return

    result = st.session_state[key]
    stage = STAGES[stage_number]

    if result["returncode"] == 0:
        stage_tests = stage.get("stage_tests")
        cumulative_tests = stage.get("cumulative_tests")

        if stage_tests is not None:
            st.success(
                f"Stage {stage_number} completed successfully. "
                f"Automated test checkpoint: {stage_tests} stage tests passed previously; "
                f"{cumulative_tests} cumulative tests verified."
            )
        else:
            st.success(f"Stage {stage_number} completed successfully.")
    else:
        st.error(
            f"Stage {stage_number} command failed with exit code "
            f"{result['returncode']}."
        )

    if stage_number == 10 and result["returncode"] == 0:
        if result.get("report_auto_opened"):
            st.info(
                "The generated HTML business report was opened automatically "
                "in your default browser. The in-page report button remains available below."
            )
        elif result.get("report_auto_open_error"):
            st.warning(
                "The report was generated, but the browser could not be opened "
                f"automatically: {result['report_auto_open_error']}. "
                "Use the in-page report button below."
            )
        else:
            st.info(
                "The report was generated. If your browser blocked the automatic "
                "new tab, use the in-page report button below."
            )

    if 2 <= stage_number <= 10:
        audit_result = result.get("audit_result")
        if audit_result is not None and audit_result["returncode"] == 0:
            if result.get("report_has_current_stage"):
                st.info(f"reports/Report.txt was refreshed and contains Stage {stage_number} evidence.")
            else:
                st.warning(
                    f"Report.txt was regenerated, but a Stage {stage_number} section was not detected. "
                    "Open the Report.txt Evidence workspace to see which sections are present."
                )
        elif audit_result is not None:
            st.warning("The stage completed, but reports/Report.txt could not be regenerated.")

    combined = result["stdout"]
    if result["stderr"]:
        combined += "\n\nSTDERR\n" + result["stderr"]
    with st.expander(
        "Stage run console output",
        expanded=st.session_state.get(
            f"stage_console_open_{stage_number}",
            False,
        ),
    ):
        st.code(
            combined.strip() or "(command returned no console output)",
            language="text",
        )


def _render_stage1_overview() -> None:
    st.subheader("Project repository path")
    st.write(
        "Select the **project root folder** on this computer. It should contain "
        "`DATASET_MANIFEST.json`, `manifest.json`, `data/`, `src/`, `sql/` and `reports/`. "
        "Do not select only the `data/` folder."
    )
    if "project_root_input" not in st.session_state:
        st.session_state["project_root_input"] = str(PROJECT_ROOT)

    path_col, browse_col = st.columns([0.80, 0.20])
    with path_col:
        st.text_input("Project root", key="project_root_input", label_visibility="collapsed")
    with browse_col:
        st.button(
            "Browse project folder", on_click=_browse_project_folder_callback,
            use_container_width=True,
        )
    if st.session_state.get("folder_dialog_error"):
        st.caption(st.session_state["folder_dialog_error"])

    if st.button("Run Stage 1 validation", type="primary", key="run_stage_1"):
        _run_stage(1)

    if "stage_1_validation_message" in st.session_state:
        success = st.session_state["stage_1_validation_success"]
        if success:
            st.success(st.session_state["stage_1_validation_message"])
        else:
            st.error(st.session_state["stage_1_validation_message"])
        frame = st.session_state["stage_1_validation"]
        if not frame.empty:
            st.dataframe(frame, use_container_width=True, hide_index=True, height=470)
            if success:
                passed = st.session_state.get("stage_1_files_passed", 0)
                st.success(
                    f"Stage 1 integrity checks: {passed}/{len(frame)} supplied files passed. "
                    "Stage 1 has no separate pytest file."
                )


def _render_overview(stage_number: int) -> None:
    stage = STAGES[stage_number]
    st.subheader("Aim and stage logic")
    st.write(stage["aim"])
    _render_implementation_files(stage_number)

    if stage_number == 1:
        _render_stage1_overview()
        return
    if stage_number == 12:
        st.info(
            "Stage 12 is a design/documentation stage. It does not mutate DuckDB and has no "
            "separate stage pytest file. The full project regression checkpoint remains 96 passed."
        )
        if PRODUCTION_DESIGN_PATH.exists():
            with st.expander("View docs/PRODUCTION_DESIGN.md", expanded=False):
                st.markdown(PRODUCTION_DESIGN_PATH.read_text(encoding="utf-8"))
        return

    st.subheader("Run and validate")
    label = "Run Stage 11 acceptance check" if stage_number == 11 else f"Run Stage {stage_number}"
    if st.button(label, type="primary", key=f"run_stage_{stage_number}"):
        with st.spinner(f"Running Stage {stage_number}..."):
            _run_stage(stage_number)
    _render_stage_run_output(stage_number)

    st.subheader("Stage results")
    result_frame = _result_dataframe(stage_number)
    if result_frame is None:
        st.info("Run/build the required database layers to display stage results.")
    elif result_frame.shape[0] == 1:
        _render_metric_row(result_frame)
        with st.expander("Result row", expanded=False):
            st.dataframe(result_frame, use_container_width=True, hide_index=True)
    else:
        st.dataframe(result_frame, use_container_width=True, hide_index=True)

    expected = stage.get("stage_tests")
    if expected is not None:
        st.caption(
            f"Stage test checkpoint: {expected} tests. "
            f"Cumulative checkpoint after this stage: {stage['cumulative_tests']} tests."
        )

    if stage_number == 11:
        if st.button("Run full 96-test regression suite", key="run_full_pytest"):
            with st.spinner("Running the full pytest suite..."):
                st.session_state["full_pytest_result"] = _run_command(
                    [sys.executable, "-m", "pytest", "-q"], timeout_seconds=1800
                )
        if "full_pytest_result" in st.session_state:
            result = st.session_state["full_pytest_result"]
            combined = (result["stdout"] + "\n" + result["stderr"]).strip()
            passed = _passed_test_count(combined)
            if result["returncode"] == 0:
                st.success(f"Full regression suite: {passed if passed is not None else 96} tests passed.")
            else:
                st.error("Full regression suite failed.")
            with st.expander("Full pytest output", expanded=False):
                st.code(combined, language="text")

    if stage_number in {10, 11}:
        st.subheader("Business report")
        _render_html_report_button(prefix=f"stage_{stage_number}")


def _render_tables(stage_number: int) -> None:
    objects = STAGES[stage_number]["objects"]
    if not objects:
        if stage_number == 1:
            st.info(
                "Stage 1 creates no DuckDB table. Its evidence is the source-file row-count "
                "and SHA256 validation shown in Overview."
            )
        elif stage_number == 11:
            st.info(
                "Stage 11 creates no new business-data table. It validates the complete existing "
                "model through acceptance checks and CI."
            )
        else:
            st.info("Stage 12 creates no DuckDB table. Its output is the production-design document.")
        return

    selector_col, detail_col = st.columns([0.27, 0.73], gap="large")
    with selector_col:
        st.subheader("Stage objects")
        selected_object = st.radio(
            "Select table/view", objects,
            key=f"object_selector_{stage_number}", label_visibility="collapsed",
        )

    with detail_col:
        st.subheader(selected_object)
        grain, purpose, expected = OBJECT_INFO.get(
            selected_object,
            ("Stage object.", "Used by this stage.", "Inspect the current rows below."),
        )
        st.markdown(f"**Grain:** {grain}")
        st.markdown(f"**Why it exists:** {purpose}")
        st.markdown(f"**Expected final result:** {expected}")

        current_rows = _object_count(selected_object)
        if current_rows is not None:
            st.metric("Current rows", f"{current_rows:,}")

        with st.expander("Creation SQL", expanded=False):
            st.code(_extract_ddl(selected_object), language="sql")

        default_query = f'SELECT *\nFROM "{selected_object}"\nLIMIT 10;'
        sql = st.text_area(
            "Editable table query", value=default_query, height=180,
            key=f"table_sql_{stage_number}_{selected_object}",
            help="The default returns 10 rows. Change fields, filters, IDs or LIMIT, then run again.",
        )
        if st.button(
            "Run table query", type="primary",
            key=f"run_table_{stage_number}_{selected_object}",
        ):
            _render_query_result(sql, key=f"table_result_{stage_number}_{selected_object}")


def _render_queries_and_audit(stage_number: int) -> None:
    if stage_number == 1:
        st.info(
            "Stage 1 creates no DuckDB table. Its evidence is the source-file row-count "
            "and SHA256 validation shown in Overview."
        )
        return

    queries = STAGE_QUERIES.get(stage_number, {})
    if not queries:
        st.info("No stage-specific database query is defined for this stage.")
        return

    st.subheader("Queries and audit traces")
    st.write(
        "Choose a stage-specific inspection/reconciliation query, edit IDs, fields or filters "
        "if needed, then run it against DuckDB."
    )
    choice = st.selectbox("Query", list(queries), key=f"merged_query_{stage_number}")
    config = queries[choice]
    st.caption(f"Type: {config['kind']}")
    st.markdown(f"**Why this query exists:** {config['purpose']}")
    st.markdown(f"**What the result shows:** {config['result']}")

    sql = st.text_area(
        "Editable SQL", value=config["sql"].strip(), height=300,
        key=f"merged_sql_{stage_number}_{choice}",
        help="Change default IDs, values, selected fields or filters before running.",
    )
    if st.button("Run query", type="primary", key=f"run_merged_{stage_number}_{choice}"):
        _render_query_result(sql, key=f"merged_result_{stage_number}_{choice}")


def _extract_report_section_v2(content: str, stage_number: int) -> str | None:
    pattern = re.compile(
        rf"(?ims)(^\s*STAGE\s+{stage_number}\b.*?)(?=^\s*STAGE\s+\d+\b|\Z)"
    )
    match = pattern.search(content)
    return match.group(1).strip() if match else None


def _report_stages_present(content: str) -> list[int]:
    return sorted({int(x) for x in re.findall(r"(?im)^\s*STAGE\s+(\d+)\b", content)})


def _render_report_evidence(current_stage: int) -> None:
    st.subheader("Cumulative Report.txt evidence")
    st.write(
        "This workspace is separate from SQL/audit queries. It shows which project-stage sections "
        "are currently present in the cumulative engineering evidence file."
    )

    if st.button("Refresh Report.txt from current model", key="refresh_report_txt"):
        with st.spinner("Regenerating reports/Report.txt..."):
            result = _refresh_audit_report()
        if result["returncode"] == 0:
            st.success("reports/Report.txt regenerated.")
        else:
            st.error("Report.txt regeneration failed.")
            with st.expander("Refresh output", expanded=True):
                st.code((result["stdout"] + "\n" + result["stderr"]).strip(), language="text")

    if not AUDIT_REPORT_PATH.exists():
        st.warning("reports/Report.txt is not available yet.")
        return

    content = AUDIT_REPORT_PATH.read_text(encoding="utf-8", errors="replace")
    present = _report_stages_present(content)

    # Only show evidence navigation from Stage 1 through the currently selected
    # stage. Later stages are hidden so the evidence view follows the walkthrough
    # progressively instead of exposing future stages.
    visible_stages = list(range(1, current_stage + 1))
    visible_present = [number for number in present if number <= current_stage]

    rows = []
    for number in visible_stages:
        rows.append({
            "stage": number,
            "name": STAGES[number]["name"],
            "evidence_in_Report.txt": "YES" if number in present else "NO",
            "current_selected_stage": "YES" if number == current_stage else "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if visible_present:
        st.caption(
            "Stage sections currently found in Report.txt up to the selected stage: "
            + ", ".join(str(x) for x in visible_present)
        )

    selected_report_stage = st.selectbox(
        "Stage evidence to inspect",
        visible_stages,
        index=len(visible_stages) - 1,
        format_func=lambda n: f"Stage {n} — {STAGES[n]['name']}",
        key=f"report_stage_selector_{current_stage}",
    )
    section = _extract_report_section_v2(content, selected_report_stage)
    if section:
        st.code(section, language="text")
    else:
        latest = max(present) if present else 0
        st.warning(
            f"Report.txt does not currently contain a Stage {selected_report_stage} section. "
            f"The latest detected stage section is Stage {latest}. Selecting a stage in the left "
            "navigation does not execute it. Run that stage in Overview, then refresh Report.txt "
            "after the required model layer exists."
        )

    # The cumulative preview also stops at the selected stage. The underlying
    # reports/Report.txt file is not modified; only the UI presentation is scoped.
    visible_sections: list[str] = []
    intro_match = re.search(r"(?is)\A(.*?)(?=^\s*STAGE\s+1\b)", content, flags=re.MULTILINE)
    if intro_match:
        visible_sections.append(intro_match.group(1).rstrip())

    for number in visible_stages:
        stage_section = _extract_report_section_v2(content, number)
        if stage_section:
            visible_sections.append(stage_section)

    visible_report_text = "\n\n".join(section for section in visible_sections if section).strip()

    with st.expander(
        f"View cumulative Report.txt through Stage {current_stage}",
        expanded=False,
    ):
        if visible_report_text:
            st.code(visible_report_text, language="text")
        else:
            st.info("No Report.txt evidence is available through the selected stage yet.")

    st.caption("Evidence file: reports/Report.txt")


def main() -> None:
    _set_page()
    selected_stage = _sidebar()
    _stage_header(selected_stage)

    workspace = st.radio(
        "Workspace",
        ["Overview", "Tables", "Queries & Audit", "Report.txt Evidence"],
        horizontal=True,
        key=f"workspace_{selected_stage}",
        label_visibility="collapsed",
    )
    st.divider()

    if workspace == "Overview":
        _render_overview(selected_stage)
    elif workspace == "Tables":
        _render_tables(selected_stage)
    elif workspace == "Queries & Audit":
        _render_queries_and_audit(selected_stage)
    else:
        _render_report_evidence(selected_stage)


if __name__ == "__main__":
    main()
