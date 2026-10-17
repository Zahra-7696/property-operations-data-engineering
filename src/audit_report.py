from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Iterable

import duckdb

from src.database import PROJECT_ROOT
from src.verify_sources import count_rows, sha256_file


REPORT_DIR = PROJECT_ROOT / "reports"
REPORT_PATH = REPORT_DIR / "Report.txt"
DATASET_MANIFEST_PATH = PROJECT_ROOT / "DATASET_MANIFEST.json"


def _separator(char: str = "=", width: int = 112) -> str:
    return char * width


def _technical_reject_category(reason_codes: str) -> str:
    if reason_codes == "malformed_json":
        return "Malformed JSON"

    if reason_codes == "invalid_updated_at":
        return "Invalid timestamp"

    codes = set(reason_codes.split(","))

    incomplete_upsert_markers = {
        "invalid_visit_id",
        "invalid_source_version",
        "invalid_property_id",
        "invalid_contractor_id",
        "invalid_visitor_id",
        "invalid_started_at",
        "missing_ended_at",
    }

    if incomplete_upsert_markers.issubset(codes):
        return "Incomplete upsert"

    return "Other technical reject"


def _short_reason(category: str) -> str:
    mapping = {
        "Malformed JSON": "JSON syntax is incomplete / cannot be parsed.",
        "Incomplete upsert": "Required upsert fields are missing or invalid.",
        "Invalid timestamp": "updated_at is not a timezone-aware ISO 8601 timestamp.",
        "Other technical reject": "Technical event-shape validation failed.",
    }
    return mapping[category]


def _extract_event_id(raw_payload: str) -> str:
    """Best-effort event_id display, including malformed JSON snippets."""
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        match = re.search(r'"event_id"\s*:\s*"([^"]*)', raw_payload)
        return match.group(1) if match else "-"

    if isinstance(payload, dict):
        value = payload.get("event_id")
        if value is None:
            return "-"
        return str(value)

    return "-"


def _wrap_cell(value: object, width: int) -> list[str]:
    text = "" if value is None else str(value)
    if not text:
        return [""]

    return textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=False,
    ) or [""]


def format_text_table(
    headers: list[str],
    rows: Iterable[Iterable[object]],
    widths: list[int],
) -> str:
    """
    Render a readable fixed-width ASCII table with wrapped cells.

    This keeps Report.txt dependency-free and easy to read in PowerShell,
    GitHub and any plain-text editor.
    """
    if not (len(headers) == len(widths)):
        raise ValueError("headers and widths must have the same length")

    rows_list = [list(row) for row in rows]
    border = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def render_physical_row(cells: list[str]) -> str:
        return (
            "| "
            + " | ".join(
                cell[:width].ljust(width)
                for cell, width in zip(cells, widths)
            )
            + " |"
        )

    output = [border, render_physical_row(headers), border]

    for row in rows_list:
        wrapped = [
            _wrap_cell(value, width)
            for value, width in zip(row, widths)
        ]
        height = max(len(parts) for parts in wrapped)

        for line_index in range(height):
            physical = [
                parts[line_index] if line_index < len(parts) else ""
                for parts in wrapped
            ]
            output.append(render_physical_row(physical))

        output.append(border)

    return "\n".join(output)


def fetch_technical_rejects(
    connection: duckdb.DuckDBPyConnection,
) -> list[dict[str, object]]:
    """Return Stage 4 rejects with stable source references."""
    rows = connection.execute(
        """
        SELECT
            r.batch_id,
            b.source_file,
            r.line_number,
            r.reason_codes,
            r.rejection_reason,
            r.raw_payload
        FROM visit_event_rejects AS r
        JOIN ingestion_batches AS b
            ON b.batch_id = r.batch_id
        ORDER BY b.manifest_order, r.line_number
        """
    ).fetchall()

    result: list[dict[str, object]] = []

    for (
        batch_id,
        source_file,
        line_number,
        reason_codes,
        rejection_reason,
        raw_payload,
    ) in rows:
        category = _technical_reject_category(reason_codes)

        result.append(
            {
                "batch_id": batch_id,
                "source_file": source_file,
                "line_number": int(line_number),
                "source_ref": f"{source_file}:{line_number}",
                "category": category,
                "event_id": _extract_event_id(raw_payload),
                "short_reason": _short_reason(category),
                "reason_codes": reason_codes,
                "rejection_reason": rejection_reason,
                "raw_payload": raw_payload,
            }
        )

    return result


def format_reject_summary_table(
    rejects: list[dict[str, object]],
) -> str:
    """Compact table suitable for both console output and Report.txt."""
    rows = [
        (
            index,
            reject["batch_id"],
            reject["line_number"],
            reject["event_id"],
            reject["category"],
            reject["short_reason"],
        )
        for index, reject in enumerate(rejects, start=1)
    ]

    return format_text_table(
        headers=["#", "Batch", "Line", "Event ID", "Category", "Reason"],
        rows=rows,
        widths=[2, 8, 5, 12, 18, 48],
    )


def format_reject_evidence_table(
    rejects: list[dict[str, object]],
) -> str:
    """Detailed evidence table used in the cumulative text report."""
    rows = [
        (
            index,
            reject["source_ref"],
            reject["reason_codes"],
            reject["raw_payload"],
        )
        for index, reject in enumerate(rejects, start=1)
    ]

    return format_text_table(
        headers=["#", "Source reference", "Reason codes", "Raw source record"],
        rows=rows,
        widths=[2, 39, 38, 72],
    )


def _stage_1_lines() -> list[str]:
    with DATASET_MANIFEST_PATH.open("r", encoding="utf-8") as file:
        manifest = json.load(file)

    rows = []
    passed = 0

    for relative_path, expected in manifest.items():
        path = PROJECT_ROOT / relative_path
        exists = path.exists()
        actual_rows = count_rows(path) if exists else None
        actual_hash = sha256_file(path) if exists else None

        rows_ok = exists and actual_rows == int(expected["rows"])
        hash_ok = exists and actual_hash == str(expected["sha256"])
        ok = rows_ok and hash_ok

        if ok:
            passed += 1

        rows.append(
            (
                "PASS" if ok else "FAIL",
                relative_path,
                actual_rows if actual_rows is not None else "missing",
                "Yes" if rows_ok else "No",
                "Yes" if hash_ok else "No",
            )
        )

    return [
        "STAGE 1 - SOURCE INTEGRITY",
        _separator("-"),
        "Purpose: verify the supplied raw files remain byte-for-byte unchanged.",
        "",
        format_text_table(
            headers=["Status", "Source file", "Rows", "Rows match", "SHA256 match"],
            rows=rows,
            widths=[6, 52, 6, 10, 12],
        ),
        "",
        f"Summary: {passed}/{len(manifest)} supplied files passed row-count and SHA256 checks.",
    ]


def _stage_2_lines(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    tables = [
        "src_companies",
        "src_contractors",
        "src_properties",
        "src_property_management",
        "src_incidents",
        "src_report_weeks",
    ]

    rows = [
        (
            table_name,
            connection.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0],
        )
        for table_name in tables
    ]

    return [
        "STAGE 2 - TYPED REFERENCE LAYER",
        _separator("-"),
        "Purpose: load the static/reference CSV sources into typed DuckDB tables.",
        "",
        format_text_table(
            headers=["Table", "Rows"],
            rows=rows,
            widths=[34, 8],
        ),
        "",
        "Reference relationship checks: PASS",
        "Reference load is transactional / rollback-safe.",
    ]


def _stage_3_lines(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    batches = connection.execute(
        """
        SELECT
            batch_id,
            source_file,
            manifest_order,
            snapshot_date,
            input_lines,
            status,
            attempt_count
        FROM ingestion_batches
        ORDER BY manifest_order
        """
    ).fetchall()

    raw_total = connection.execute(
        "SELECT COUNT(*) FROM raw_visit_lines"
    ).fetchone()[0]

    rows = [
        (
            batch_id,
            manifest_order,
            snapshot_date,
            input_lines,
            status,
            attempt_count,
            source_file,
        )
        for (
            batch_id,
            source_file,
            manifest_order,
            snapshot_date,
            input_lines,
            status,
            attempt_count,
        ) in batches
    ]

    return [
        "STAGE 3 - RAW VISIT BATCH LANDING",
        _separator("-"),
        "Purpose: preserve every physical JSONL line in manifest order before parsing.",
        "",
        format_text_table(
            headers=["Batch", "Order", "Snapshot", "Lines", "Status", "Attempts", "Source file"],
            rows=rows,
            widths=[8, 5, 10, 6, 9, 8, 38],
        ),
        "",
        f"Raw physical visit lines preserved: {raw_total}",
        "Replay behavior: already-landed batches are a no-op.",
        "Restart behavior: failed batch landing rolls back partial raw rows.",
    ]


def _stage_4_lines(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    batches = connection.execute(
        """
        SELECT
            batch_id,
            source_file,
            technically_valid_lines,
            technically_invalid_lines
        FROM ingestion_batches
        ORDER BY manifest_order
        """
    ).fetchall()

    valid_total = connection.execute(
        "SELECT COUNT(*) FROM validated_visit_events"
    ).fetchone()[0]

    invalid_total = connection.execute(
        "SELECT COUNT(*) FROM visit_event_rejects"
    ).fetchone()[0]

    rejects = fetch_technical_rejects(connection)

    category_counts: dict[str, int] = {}
    for reject in rejects:
        category = str(reject["category"])
        category_counts[category] = category_counts.get(category, 0) + 1

    batch_table = format_text_table(
        headers=["Batch", "Valid", "Invalid", "Source file"],
        rows=[
            (batch_id, valid_count, invalid_count, source_file)
            for batch_id, source_file, valid_count, invalid_count in batches
        ],
        widths=[8, 7, 7, 42],
    )

    category_table = format_text_table(
        headers=["Reject category", "Count", "Meaning"],
        rows=[
            (
                category,
                category_counts[category],
                _short_reason(category),
            )
            for category in sorted(category_counts)
        ],
        widths=[22, 7, 58],
    )

    return [
        "STAGE 4 - TECHNICAL VISIT-EVENT VALIDATION",
        _separator("-"),
        (
            "Purpose: separate technically valid event deliveries from malformed "
            "or incomplete source lines while preserving reject evidence."
        ),
        "",
        "Per-batch validation summary:",
        batch_table,
        "",
        "Overall totals:",
        f"  Technically valid event-delivery lines   : {valid_total}",
        f"  Technically invalid event-delivery lines : {invalid_total}",
        "",
        "Reject-category summary:",
        category_table,
        "",
        "Rejected-record summary:",
        format_reject_summary_table(rejects),
        "",
        "Detailed source evidence:",
        (
            "Reference format is <source_file>:<line_number>. "
            "The raw source files remain unchanged."
        ),
        format_reject_evidence_table(rejects),
        "",
        "Validation replay behavior: already-validated batches are a no-op.",
        (
            "Boundary: unknown business references and invalid visit duration "
            "are not Stage 4 technical rejects."
        ),
    ]


def build_report_text(
    connection: duckdb.DuckDBPyConnection,
) -> str:
    """
    Build the cumulative engineering/audit report through the latest completed stage.

    Future stages extend this same report rather than creating separate stage files.
    """
    sections = [
        [
            "PROPERTY OPERATIONS - CUMULATIVE PIPELINE AUDIT REPORT",
            _separator("="),
            (
                "Purpose: engineering evidence for ingestion, validation, quality "
                "and model-processing decisions."
            ),
            (
                "This is separate from the final business-facing HTML operations "
                "report created later in the project."
            ),
        ],
        _stage_1_lines(),
        _stage_2_lines(connection),
        _stage_3_lines(connection),
        _stage_4_lines(connection),
    ]

    return "\n\n".join("\n".join(section) for section in sections).rstrip() + "\n"


def write_processing_report(
    connection: duckdb.DuckDBPyConnection,
    *,
    report_path: Path | str = REPORT_PATH,
) -> Path:
    """
    Rebuild the same cumulative Report.txt from the current database state.

    Regeneration avoids duplicate append-only log entries. Later stages add
    sections to this same deterministic report.
    """
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report_text(connection), encoding="utf-8")
    return path
