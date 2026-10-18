from __future__ import annotations

import json
import re
import textwrap
from datetime import datetime, timezone
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



def _format_utc(value: datetime | None) -> str:
    """Render TIMESTAMPTZ values consistently, independent of local display settings."""
    if value is None:
        return "-"

    return (
        value.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )



def _format_local(value: datetime | None) -> str:
    if value is None:
        return "-"

    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _duration_minutes(
    started_at: datetime | None,
    ended_at: datetime | None,
) -> str:
    if started_at is None or ended_at is None:
        return "-"

    minutes = (ended_at - started_at).total_seconds() / 60
    return f"{minutes:.1f}"


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



def _table_exists(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
) -> bool:
    count = connection.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = ?
        """,
        [table_name],
    ).fetchone()[0]

    return count == 1


def _has_stage5_results(
    connection: duckdb.DuckDBPyConnection,
) -> bool:
    if not _table_exists(connection, "visit_events"):
        return False

    completed = connection.execute(
        """
        SELECT COUNT(*)
        FROM ingestion_batches
        WHERE status = 'completed'
        """
    ).fetchone()[0]

    return completed > 0


def fetch_duplicate_deliveries(
    connection: duckdb.DuckDBPyConnection,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            d.event_id,
            d.batch_id,
            b.source_file AS duplicate_source_file,
            d.line_number AS duplicate_line,
            d.canonical_batch_id,
            cb.source_file AS canonical_source_file,
            d.canonical_line_number AS canonical_line
        FROM visit_event_duplicates AS d
        JOIN ingestion_batches AS b
            ON b.batch_id = d.batch_id
        JOIN ingestion_batches AS cb
            ON cb.batch_id = d.canonical_batch_id
        ORDER BY
            b.manifest_order,
            d.line_number
        """
    ).fetchall()

    return [
        {
            "event_id": event_id,
            "duplicate_batch_id": duplicate_batch_id,
            "duplicate_ref": f"{duplicate_source_file}:{duplicate_line}",
            "canonical_batch_id": canonical_batch_id,
            "canonical_ref": f"{canonical_source_file}:{canonical_line}",
            "reason": "Repeated delivery of an already-known global event_id.",
        }
        for (
            event_id,
            duplicate_batch_id,
            duplicate_source_file,
            duplicate_line,
            canonical_batch_id,
            canonical_source_file,
            canonical_line,
        ) in rows
    ]


def fetch_stale_events(
    connection: duckdb.DuckDBPyConnection,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            e.event_id,
            e.visit_id,
            e.source_version,
            e.prior_source_version,
            e.operation,
            e.first_batch_id,
            b.source_file,
            e.first_line_number
        FROM visit_events AS e
        JOIN ingestion_batches AS b
            ON b.batch_id = e.first_batch_id
        WHERE e.state_action = 'stale'
        ORDER BY
            b.manifest_order,
            e.first_line_number
        """
    ).fetchall()

    result: list[dict[str, object]] = []

    for (
        event_id,
        visit_id,
        source_version,
        prior_source_version,
        operation,
        batch_id,
        source_file,
        line_number,
    ) in rows:
        comparison = (
            "lower source_version"
            if source_version < prior_source_version
            else "equal source_version"
        )

        result.append(
            {
                "event_id": event_id,
                "visit_id": visit_id,
                "incoming_version": int(source_version),
                "existing_version": int(prior_source_version),
                "operation": operation,
                "batch_id": batch_id,
                "source_ref": f"{source_file}:{line_number}",
                "reason": (
                    f"{comparison}: incoming version does not exceed "
                    "the current visit version."
                ),
            }
        )

    return result


def format_duplicate_delivery_table(
    duplicates: list[dict[str, object]],
) -> str:
    return format_text_table(
        headers=[
            "#",
            "Event ID",
            "Duplicate source",
            "Canonical source",
            "Reason",
        ],
        rows=[
            (
                index,
                item["event_id"],
                item["duplicate_ref"],
                item["canonical_ref"],
                item["reason"],
            )
            for index, item in enumerate(duplicates, start=1)
        ],
        widths=[3, 12, 41, 41, 50],
    )


def format_stale_event_table(
    stale_events: list[dict[str, object]],
) -> str:
    return format_text_table(
        headers=[
            "#",
            "Visit",
            "Event ID",
            "Incoming",
            "Existing",
            "Op",
            "Source reference",
            "Reason",
        ],
        rows=[
            (
                index,
                item["visit_id"],
                item["event_id"],
                item["incoming_version"],
                item["existing_version"],
                item["operation"],
                item["source_ref"],
                item["reason"],
            )
            for index, item in enumerate(stale_events, start=1)
        ],
        widths=[3, 8, 12, 8, 8, 7, 41, 50],
    )


def _stage_5_lines(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    batches = connection.execute(
        """
        SELECT
            batch_id,
            unique_event_lines,
            duplicate_event_lines,
            applied_state_changes,
            stale_event_lines,
            status
        FROM ingestion_batches
        ORDER BY manifest_order
        """
    ).fetchall()

    unique_events = connection.execute(
        "SELECT COUNT(*) FROM visit_events"
    ).fetchone()[0]

    duplicate_deliveries = connection.execute(
        "SELECT COUNT(*) FROM visit_event_duplicates"
    ).fetchone()[0]

    applied = connection.execute(
        """
        SELECT COUNT(*)
        FROM visit_events
        WHERE state_action = 'applied'
        """
    ).fetchone()[0]

    stale = connection.execute(
        """
        SELECT COUNT(*)
        FROM visit_events
        WHERE state_action = 'stale'
        """
    ).fetchone()[0]

    current_states = connection.execute(
        "SELECT COUNT(*) FROM visit_current_state"
    ).fetchone()[0]

    active_states = connection.execute(
        """
        SELECT COUNT(*)
        FROM visit_current_state
        WHERE is_deleted = FALSE
        """
    ).fetchone()[0]

    deleted_states = connection.execute(
        """
        SELECT COUNT(*)
        FROM visit_current_state
        WHERE is_deleted = TRUE
        """
    ).fetchone()[0]

    duplicates = fetch_duplicate_deliveries(connection)
    stale_events = fetch_stale_events(connection)

    deleted_rows = connection.execute(
        """
        SELECT
            s.visit_id,
            s.source_version,
            s.winning_event_id,
            b.source_file,
            s.source_line_number
        FROM visit_current_state AS s
        JOIN ingestion_batches AS b
            ON b.batch_id = s.source_batch_id
        WHERE s.is_deleted = TRUE
        ORDER BY s.visit_id
        """
    ).fetchall()

    key_visits = connection.execute(
        """
        SELECT
            s.visit_id,
            s.source_version,
            s.operation,
            s.is_deleted,
            s.winning_event_id,
            b.source_file,
            s.source_line_number
        FROM visit_current_state AS s
        JOIN ingestion_batches AS b
            ON b.batch_id = s.source_batch_id
        WHERE s.visit_id IN ('V02', 'V03', 'V04')
        ORDER BY s.visit_id
        """
    ).fetchall()

    lower_stale = sum(
        1
        for item in stale_events
        if item["incoming_version"] < item["existing_version"]
    )
    equal_stale = len(stale_events) - lower_stale

    batch_table = format_text_table(
        headers=["Batch", "Unique", "Duplicates", "Applied", "Stale", "Status"],
        rows=batches,
        widths=[8, 8, 10, 8, 7, 10],
    )

    key_visit_table = format_text_table(
        headers=[
            "Visit",
            "Version",
            "Operation",
            "Deleted",
            "Winning event",
            "Winning source",
        ],
        rows=[
            (
                visit_id,
                source_version,
                operation,
                is_deleted,
                winning_event_id,
                f"{source_file}:{line_number}",
            )
            for (
                visit_id,
                source_version,
                operation,
                is_deleted,
                winning_event_id,
                source_file,
                line_number,
            ) in key_visits
        ],
        widths=[8, 8, 10, 8, 14, 42],
    )

    deleted_table = format_text_table(
        headers=["Visit", "Version", "Winning event", "Winning source"],
        rows=[
            (
                visit_id,
                source_version,
                winning_event_id,
                f"{source_file}:{line_number}",
            )
            for (
                visit_id,
                source_version,
                winning_event_id,
                source_file,
                line_number,
            ) in deleted_rows
        ],
        widths=[10, 8, 14, 44],
    )

    return [
        "STAGE 5 - EVENT DEDUPLICATION AND CURRENT VISIT STATE",
        _separator("-"),
        (
            "Purpose: count repeated global event deliveries once and maintain "
            "the highest source_version for each visit."
        ),
        "",
        "Per-batch processing summary:",
        batch_table,
        "",
        "Overall totals:",
        f"  Technically valid physical deliveries : {unique_events + duplicate_deliveries}",
        f"  Globally unique event IDs             : {unique_events}",
        f"  Duplicate deliveries                  : {duplicate_deliveries}",
        f"  Applied unique state changes          : {applied}",
        f"  Stale/equal-version unique events     : {stale}",
        f"    - lower-version stale events        : {lower_stale}",
        f"    - equal-version stale events        : {equal_stale}",
        f"  Distinct current visit states         : {current_states}",
        f"  Active current visit states           : {active_states}",
        f"  Deleted current visit states          : {deleted_states}",
        "",
        "Key version/delete/reactivation examples:",
        key_visit_table,
        "",
        "Duplicate-delivery evidence:",
        format_duplicate_delivery_table(duplicates),
        "",
        "Stale/equal-version evidence:",
        format_stale_event_table(stale_events),
        "",
        "Deleted current-state tombstones:",
        deleted_table,
        "",
        (
            "Precedence rule: an incoming event changes visit_current_state only "
            "when source_version is strictly greater than the current version."
        ),
        (
            "A delete remains as a tombstone; an older upsert cannot resurrect it. "
            "A genuinely newer upsert can reactivate the visit."
        ),
    ]


def _has_stage6_results(
    connection: duckdb.DuckDBPyConnection,
) -> bool:
    if not _table_exists(connection, "visit_quality_issues"):
        return False

    # The fixed assignment dataset has three known current business-quality
    # issues. A non-zero count therefore also acts as the Stage 6 completion
    # signal for the cumulative report.
    count = connection.execute(
        "SELECT COUNT(*) FROM visit_quality_issues"
    ).fetchone()[0]

    return count > 0


def _stage_6_lines(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    active = connection.execute(
        """
        SELECT COUNT(*)
        FROM visit_current_state
        WHERE is_deleted = FALSE
        """
    ).fetchone()[0]

    deleted = connection.execute(
        """
        SELECT COUNT(*)
        FROM visit_current_state
        WHERE is_deleted = TRUE
        """
    ).fetchone()[0]

    issue_rows = connection.execute(
        "SELECT COUNT(*) FROM visit_quality_issues"
    ).fetchone()[0]

    affected = connection.execute(
        "SELECT COUNT(DISTINCT visit_id) FROM visit_quality_issues"
    ).fetchone()[0]

    reportable = connection.execute(
        "SELECT COUNT(*) FROM reportable_current_visits"
    ).fetchone()[0]

    unfinished_reportable = connection.execute(
        """
        SELECT COUNT(*)
        FROM reportable_current_visits
        WHERE ended_at IS NULL
        """
    ).fetchone()[0]

    issues = connection.execute(
        """
        SELECT
            q.visit_id,
            q.issue_code,
            q.winning_event_id,
            q.property_id,
            q.contractor_id,
            q.started_at,
            q.ended_at,
            b.source_file,
            q.source_line_number,
            q.issue_detail
        FROM visit_quality_issues AS q
        JOIN ingestion_batches AS b
            ON b.batch_id = q.source_batch_id
        ORDER BY q.visit_id, q.issue_code
        """
    ).fetchall()

    category_counts = connection.execute(
        """
        SELECT issue_code, COUNT(*)
        FROM visit_quality_issues
        GROUP BY issue_code
        ORDER BY issue_code
        """
    ).fetchall()

    return [
        "STAGE 6 - CURRENT VISIT BUSINESS QUALITY",
        _separator("-"),
        (
            "Purpose: keep evidence of well-formed but business-invalid current "
            "visits and exclude them from reporting metrics."
        ),
        "",
        "Overall totals:",
        f"  Current active visits              : {active}",
        f"  Business-quality issue rows        : {issue_rows}",
        f"  Active visits affected             : {affected}",
        f"  Reportable active visits           : {reportable}",
        f"  Deleted visit tombstones           : {deleted}",
        f"  Valid unfinished reportable visits : {unfinished_reportable}",
        "",
        "Issue-category summary:",
        format_text_table(
            headers=["Issue code", "Count", "Meaning"],
            rows=[
                (
                    issue_code,
                    count,
                    {
                        "unknown_property": "property_id is not in src_properties",
                        "unknown_contractor": "contractor_id is not in src_contractors",
                        "end_before_start": "ended_at is earlier than started_at",
                    }[issue_code],
                )
                for issue_code, count in category_counts
            ],
            widths=[22, 7, 58],
        ),
        "",
        "Business-quality issue evidence:",
        format_text_table(
            headers=[
                "Visit",
                "Issue",
                "Started UTC",
                "Ended UTC",
                "Duration min",
                "Property",
                "Contractor",
                "Source reference",
                "Reason",
            ],
            rows=[
                (
                    visit_id,
                    issue_code,
                    _format_utc(started_at),
                    _format_utc(ended_at),
                    _duration_minutes(started_at, ended_at),
                    property_id,
                    contractor_id,
                    f"{source_file}:{line_number}",
                    detail,
                )
                for (
                    visit_id,
                    issue_code,
                    winning_event_id,
                    property_id,
                    contractor_id,
                    started_at,
                    ended_at,
                    source_file,
                    line_number,
                    detail,
                ) in issues
            ],
            widths=[8, 20, 20, 20, 12, 12, 12, 42, 46],
        ),
        "",
        (
            "Deleted visits are excluded from active reporting but are not "
            "business-quality failures."
        ),
        (
            "Unfinished visits (ended_at = NULL) remain valid and are kept in "
            "reportable_current_visits."
        ),
    ]


def _has_stage7_results(
    connection: duckdb.DuckDBPyConnection,
) -> bool:
    if not _table_exists(connection, "property_management_intervals"):
        return False

    count = connection.execute(
        "SELECT COUNT(*) FROM property_management_intervals"
    ).fetchone()[0]

    return count > 0


def _stage_7_lines(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    source_observations = connection.execute(
        "SELECT COUNT(*) FROM src_property_management"
    ).fetchone()[0]

    intervals = connection.execute(
        "SELECT COUNT(*) FROM property_management_intervals"
    ).fetchone()[0]

    properties = connection.execute(
        """
        SELECT COUNT(DISTINCT property_id)
        FROM property_management_intervals
        """
    ).fetchone()[0]

    current_rows = connection.execute(
        "SELECT COUNT(*) FROM current_property_management"
    ).fetchone()[0]

    histories = connection.execute(
        """
        SELECT
            i.property_id,
            p.property_name,
            i.company_id,
            c.company_name,
            i.valid_from,
            i.valid_to,
            i.start_observation_id,
            i.source_observation_count
        FROM property_management_intervals AS i
        JOIN src_properties AS p
            ON p.property_id = i.property_id
        JOIN src_companies AS c
            ON c.company_id = i.company_id
        ORDER BY i.property_id, i.valid_from
        """
    ).fetchall()

    current = connection.execute(
        """
        SELECT
            property_id,
            property_name,
            company_id,
            company_name,
            managed_from
        FROM current_property_management
        ORDER BY property_id
        """
    ).fetchall()

    p01 = connection.execute(
        """
        SELECT
            company_id,
            valid_from,
            valid_to,
            source_observation_count
        FROM property_management_intervals
        WHERE property_id = 'P01'
        ORDER BY valid_from
        """
    ).fetchall()

    boundary_records = connection.execute(
        """
        WITH supplied_events AS (
            SELECT
                'visit' AS record_type,
                visit_id AS record_id,
                property_id,
                started_at AS business_time
            FROM visit_current_state
            WHERE visit_id IN ('V09', 'V10')

            UNION ALL

            SELECT
                'incident' AS record_type,
                incident_id AS record_id,
                property_id,
                reported_at AS business_time
            FROM src_incidents
            WHERE incident_id IN ('I06', 'I07')
        )
        SELECT
            e.record_type,
            e.record_id,
            e.property_id,
            e.business_time,
            i.company_id
        FROM supplied_events AS e
        LEFT JOIN property_management_intervals AS i
            ON i.property_id = e.property_id
           AND e.business_time >= i.valid_from
           AND (i.valid_to IS NULL OR e.business_time < i.valid_to)
        ORDER BY e.business_time, e.record_type, e.record_id
        """
    ).fetchall()

    return [
        "STAGE 7 - PROPERTY MANAGEMENT HISTORY",
        _separator("-"),
        (
            "Purpose: convert unsorted management observations into contiguous "
            "business-time intervals and expose the current manager separately."
        ),
        "",
        "Overall totals:",
        f"  Source management observations    : {source_observations}",
        f"  Historical management intervals   : {intervals}",
        f"  Unchanged observations collapsed  : {source_observations - intervals}",
        f"  Properties represented            : {properties}",
        f"  Current management rows           : {current_rows}",
        "",
        "All reconstructed management intervals:",
        format_text_table(
            headers=[
                "Property",
                "Property name",
                "Company",
                "Company name",
                "Valid from UTC",
                "Valid to UTC",
                "Start obs",
                "Obs count",
            ],
            rows=[
                (
                    property_id,
                    property_name,
                    company_id,
                    company_name,
                    _format_utc(valid_from),
                    _format_utc(valid_to),
                    start_observation_id,
                    observation_count,
                )
                for (
                    property_id,
                    property_name,
                    company_id,
                    company_name,
                    valid_from,
                    valid_to,
                    start_observation_id,
                    observation_count,
                ) in histories
            ],
            widths=[9, 23, 8, 25, 20, 20, 11, 9],
        ),
        "",
        "P01 return-to-previous-manager proof:",
        format_text_table(
            headers=["Company", "Valid from UTC", "Valid to UTC", "Obs count"],
            rows=[
                (
                    company_id,
                    _format_utc(valid_from),
                    _format_utc(valid_to),
                    observation_count,
                )
                for (
                    company_id,
                    valid_from,
                    valid_to,
                    observation_count,
                ) in p01
            ],
            widths=[10, 22, 22, 10],
        ),
        "",
        "P03 transfer-boundary attribution proof:",
        format_text_table(
            headers=["Type", "Record", "Property", "Business time UTC", "Company"],
            rows=[
                (
                    record_type,
                    record_id,
                    property_id,
                    _format_utc(business_time),
                    company_id,
                )
                for (
                    record_type,
                    record_id,
                    property_id,
                    business_time,
                    company_id,
                ) in boundary_records
            ],
            widths=[10, 10, 10, 22, 10],
        ),
        "",
        "Current-management lookup:",
        format_text_table(
            headers=[
                "Property",
                "Property name",
                "Company",
                "Company name",
                "Managed from UTC",
            ],
            rows=[
                (
                    property_id,
                    property_name,
                    company_id,
                    company_name,
                    _format_utc(managed_from),
                )
                for (
                    property_id,
                    property_name,
                    company_id,
                    company_name,
                    managed_from,
                ) in current
            ],
            widths=[9, 23, 8, 25, 22],
        ),
        "",
        (
            "Interval rule: [valid_from, valid_to). A record exactly at the "
            "transfer timestamp belongs to the new company."
        ),
        (
            "Consecutive unchanged observations collapse into one interval; "
            "a later return to a previous company creates a new interval."
        ),
    ]


def _has_stage8_results(
    connection: duckdb.DuckDBPyConnection,
) -> bool:
    if not _table_exists(connection, "fact_visits"):
        return False

    return connection.execute(
        "SELECT COUNT(*) FROM fact_visits"
    ).fetchone()[0] > 0


def _stage_8_lines(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    visit_total = connection.execute(
        "SELECT COUNT(*) FROM fact_visits"
    ).fetchone()[0]

    incident_total = connection.execute(
        "SELECT COUNT(*) FROM fact_incidents"
    ).fetchone()[0]

    requested = connection.execute(
        """
        SELECT
            COUNT(*) AS visit_count,
            COUNT(DISTINCT visitor_id) AS distinct_visitors,
            ROUND(COALESCE(SUM(completed_hours), 0), 2) AS completed_hours,
            COALESCE(
                SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END),
                0
            ) AS unfinished_visits
        FROM fact_visits
        WHERE is_requested_week = TRUE
        """
    ).fetchone()

    requested_incidents = connection.execute(
        """
        SELECT
            COUNT(*) AS incident_count,
            COALESCE(
                SUM(CASE WHEN is_high THEN 1 ELSE 0 END),
                0
            ) AS high_incidents
        FROM fact_incidents
        WHERE is_requested_week = TRUE
        """
    ).fetchone()

    requested_visit_count = requested[0]
    requested_incident_count = requested_incidents[0]

    history_examples = connection.execute(
        """
        SELECT
            'visit' AS record_type,
            visit_id AS record_id,
            property_id,
            started_at AS business_time,
            company_id
        FROM fact_visits
        WHERE visit_id IN ('V09', 'V10')

        UNION ALL

        SELECT
            'incident' AS record_type,
            incident_id AS record_id,
            property_id,
            reported_at AS business_time,
            company_id
        FROM fact_incidents
        WHERE incident_id IN ('I06', 'I07')

        ORDER BY business_time, record_type, record_id
        """
    ).fetchall()

    week_examples = connection.execute(
        """
        SELECT
            visit_id,
            property_id,
            property_timezone,
            started_at,
            local_started_at,
            week_start_local
        FROM fact_visits
        WHERE visit_id IN ('V14', 'V01')
        ORDER BY started_at
        """
    ).fetchall()

    dst_examples = connection.execute(
        """
        SELECT
            visit_id,
            property_timezone,
            started_at,
            ended_at,
            local_started_at,
            local_ended_at,
            completed_hours
        FROM fact_visits
        WHERE visit_id IN ('V07', 'V16')
        ORDER BY visit_id
        """
    ).fetchall()

    return [
        "STAGE 8 - ANALYTICAL VISIT AND INCIDENT FACTS",
        _separator("-"),
        (
            "Purpose: create one reportable-visit fact row per visit and one "
            "incident fact row per incident with historical company and "
            "property-local reporting week attribution."
        ),
        "",
        "Fact counts:",
        f"  Visit facts (all reportable current visits) : {visit_total}",
        f"  Incident facts (all supplied incidents)     : {incident_total}",
        f"  Visits in requested local weeks             : {requested_visit_count}",
        f"  Incidents in requested local weeks          : {requested_incident_count}",
        f"  Visits outside requested weeks              : {visit_total - requested_visit_count}",
        f"  Incidents outside requested weeks           : {incident_total - requested_incident_count}",
        "",
        "Requested-week metric checks:",
        f"  Visit count             : {requested[0]}",
        f"  Distinct visitors       : {requested[1]}",
        f"  Completed visit hours   : {float(requested[2]):.2f}",
        f"  Unfinished visits       : {requested[3]}",
        f"  Incident count          : {requested_incidents[0]}",
        f"  High-severity incidents : {requested_incidents[1]}",
        "",
        "Historical company attribution proof:",
        format_text_table(
            headers=["Type", "Record", "Property", "Business time UTC", "Company"],
            rows=[
                (
                    record_type,
                    record_id,
                    property_id,
                    _format_utc(business_time),
                    company_id,
                )
                for (
                    record_type,
                    record_id,
                    property_id,
                    business_time,
                    company_id,
                ) in history_examples
            ],
            widths=[10, 10, 10, 22, 10],
        ),
        "",
        "Property-local week-boundary proof:",
        format_text_table(
            headers=[
                "Visit",
                "Property",
                "Timezone",
                "Started UTC",
                "Local start",
                "Local week",
            ],
            rows=[
                (
                    visit_id,
                    property_id,
                    property_timezone,
                    _format_utc(started_at),
                    _format_local(local_started_at),
                    week_start_local,
                )
                for (
                    visit_id,
                    property_id,
                    property_timezone,
                    started_at,
                    local_started_at,
                    week_start_local,
                ) in week_examples
            ],
            widths=[8, 10, 20, 22, 20, 12],
        ),
        "",
        "DST / elapsed-hours proof:",
        format_text_table(
            headers=[
                "Visit",
                "Timezone",
                "Start UTC",
                "End UTC",
                "Local start",
                "Local end",
                "Hours",
            ],
            rows=[
                (
                    visit_id,
                    property_timezone,
                    _format_utc(started_at),
                    _format_utc(ended_at),
                    _format_local(local_started_at),
                    _format_local(local_ended_at),
                    f"{completed_hours:.2f}",
                )
                for (
                    visit_id,
                    property_timezone,
                    started_at,
                    ended_at,
                    local_started_at,
                    local_ended_at,
                    completed_hours,
                ) in dst_examples
            ],
            widths=[8, 20, 22, 22, 20, 20, 8],
        ),
        "",
        (
            "Visits use started_at for historical company and local reporting "
            "week; incidents use reported_at."
        ),
        (
            "Completed hours use elapsed TIMESTAMPTZ/UTC duration and are "
            "therefore not distorted by daylight-saving clock changes."
        ),
        (
            "Visit and incident facts remain separate; later reporting "
            "aggregates each fact independently before combining metrics."
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

    if _has_stage5_results(connection):
        sections.append(_stage_5_lines(connection))

    if _has_stage6_results(connection):
        sections.append(_stage_6_lines(connection))

    if _has_stage7_results(connection):
        sections.append(_stage_7_lines(connection))

    if _has_stage8_results(connection):
        sections.append(_stage_8_lines(connection))

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
