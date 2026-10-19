from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.audit_report import format_text_table
from src.database import PROJECT_ROOT, connect_database


REPORT_PATH = Path("reports/operations_report.html")
AUDIT_REPORT_PATH = Path("reports/Report.txt")

EXPECTED_EXTRACT_ROWS: dict[str, int] = {
    "reports/extracts/company_week.csv": 18,
    "reports/extracts/company_period.csv": 3,
    "reports/extracts/property_week.csv": 144,
    "reports/extracts/contractor.csv": 8,
    "reports/extracts/data_quality_summary.csv": 8,
}


@dataclass(frozen=True)
class DatabaseCheck:
    label: str
    query: str
    expected: object
    transform: Callable[[object], object] = lambda value: value


DB_CHECKS: tuple[DatabaseCheck, ...] = (
    DatabaseCheck(
        "Raw visit lines",
        "SELECT COUNT(*) FROM raw_visit_lines",
        1997,
    ),
    DatabaseCheck(
        "Technical rejects",
        "SELECT COUNT(*) FROM visit_event_rejects",
        18,
    ),
    DatabaseCheck(
        "Unique visit events",
        "SELECT COUNT(*) FROM visit_events",
        1944,
    ),
    DatabaseCheck(
        "Duplicate deliveries",
        "SELECT COUNT(*) FROM visit_event_duplicates",
        35,
    ),
    DatabaseCheck(
        "Current visit states",
        "SELECT COUNT(*) FROM visit_current_state",
        1618,
    ),
    DatabaseCheck(
        "Deleted tombstones",
        """
        SELECT COUNT(*)
        FROM visit_current_state
        WHERE is_deleted = TRUE
        """,
        51,
    ),
    DatabaseCheck(
        "Reportable current visits",
        "SELECT COUNT(*) FROM reportable_current_visits",
        1564,
    ),
    DatabaseCheck(
        "Management intervals",
        "SELECT COUNT(*) FROM property_management_intervals",
        37,
    ),
    DatabaseCheck(
        "Visit facts",
        "SELECT COUNT(*) FROM fact_visits",
        1564,
    ),
    DatabaseCheck(
        "Incident facts",
        "SELECT COUNT(*) FROM fact_incidents",
        247,
    ),
    DatabaseCheck(
        "Requested-week visits",
        """
        SELECT COUNT(*)
        FROM fact_visits
        WHERE is_requested_week = TRUE
        """,
        1543,
    ),
    DatabaseCheck(
        "Requested-week incidents",
        """
        SELECT COUNT(*)
        FROM fact_incidents
        WHERE is_requested_week = TRUE
        """,
        246,
    ),
    DatabaseCheck(
        "Company/week mart rows",
        "SELECT COUNT(*) FROM rpt_company_week",
        18,
    ),
    DatabaseCheck(
        "Company-period mart rows",
        "SELECT COUNT(*) FROM rpt_company_period",
        3,
    ),
    DatabaseCheck(
        "Property/week mart rows",
        "SELECT COUNT(*) FROM rpt_property_week",
        144,
    ),
    DatabaseCheck(
        "Zero-activity property/weeks",
        """
        SELECT COUNT(*)
        FROM rpt_property_week
        WHERE has_activity = FALSE
        """,
        22,
    ),
    DatabaseCheck(
        "Contractor mart rows",
        "SELECT COUNT(*) FROM rpt_contractor",
        8,
    ),
    DatabaseCheck(
        "Report build rows",
        "SELECT COUNT(*) FROM report_builds",
        1,
    ),
)


def _count_csv_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as file:
        return sum(1 for _ in csv.DictReader(file))


def run_acceptance_checks() -> None:
    """
    Validate the final rebuilt repository outputs.

    This is intentionally a compact acceptance gate rather than another
    transformation stage. It checks the expected cross-layer row counts,
    latest completed batch and committed/viewable report artifacts.
    """
    connection = connect_database(read_only=True)

    results: list[tuple[object, ...]] = []
    failures: list[str] = []

    try:
        for check in DB_CHECKS:
            value = connection.execute(check.query).fetchone()[0]
            actual = check.transform(value)
            passed = actual == check.expected

            results.append(
                (
                    check.label,
                    check.expected,
                    actual,
                    "PASS" if passed else "FAIL",
                )
            )

            if not passed:
                failures.append(
                    f"{check.label}: expected {check.expected!r}, got {actual!r}"
                )

        latest_batch = connection.execute(
            """
            SELECT batch_id
            FROM ingestion_batches
            WHERE status = 'completed'
            ORDER BY manifest_order DESC
            LIMIT 1
            """
        ).fetchone()

        latest_batch_id = None if latest_batch is None else latest_batch[0]
        latest_batch_passed = latest_batch_id == "batch_06"

        results.append(
            (
                "Latest completed batch",
                "batch_06",
                latest_batch_id,
                "PASS" if latest_batch_passed else "FAIL",
            )
        )

        if not latest_batch_passed:
            failures.append(
                "Latest completed batch: "
                f"expected 'batch_06', got {latest_batch_id!r}"
            )

    finally:
        connection.close()

    report_path = PROJECT_ROOT / REPORT_PATH
    audit_path = PROJECT_ROOT / AUDIT_REPORT_PATH

    report_ok = report_path.exists() and report_path.stat().st_size > 50_000
    results.append(
        (
            "HTML operations report",
            "exists and > 50 KB",
            (
                f"{report_path.stat().st_size:,} bytes"
                if report_path.exists()
                else "missing"
            ),
            "PASS" if report_ok else "FAIL",
        )
    )
    if not report_ok:
        failures.append(
            "HTML operations report is missing or unexpectedly small"
        )

    audit_ok = audit_path.exists() and audit_path.stat().st_size > 0
    results.append(
        (
            "Cumulative audit report",
            "exists and non-empty",
            (
                f"{audit_path.stat().st_size:,} bytes"
                if audit_path.exists()
                else "missing"
            ),
            "PASS" if audit_ok else "FAIL",
        )
    )
    if not audit_ok:
        failures.append("Cumulative audit report is missing or empty")

    for relative_path, expected_rows in EXPECTED_EXTRACT_ROWS.items():
        path = PROJECT_ROOT / relative_path
        actual_rows = _count_csv_rows(path) if path.exists() else None
        passed = actual_rows == expected_rows

        results.append(
            (
                relative_path,
                expected_rows,
                actual_rows if actual_rows is not None else "missing",
                "PASS" if passed else "FAIL",
            )
        )

        if not passed:
            failures.append(
                f"{relative_path}: expected {expected_rows} data rows, "
                f"got {actual_rows!r}"
            )

    print("=" * 112)
    print("FINAL REPOSITORY ACCEPTANCE CHECK")
    print("=" * 112)
    print(
        "This gate verifies that a clean rebuild produced the expected model "
        "layers and final report artifacts."
    )
    print()
    print(
        format_text_table(
            headers=["Check", "Expected", "Actual", "Status"],
            rows=results,
            widths=[42, 22, 22, 8],
        )
    )

    if failures:
        print()
        print("FAILURES")
        print("-" * 112)
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)

    print()
    print(
        "SUCCESS: Final model counts, latest batch, HTML report, audit report "
        "and CSV extracts all passed acceptance checks."
    )


def main() -> None:
    run_acceptance_checks()


if __name__ == "__main__":
    main()
