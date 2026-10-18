from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.audit_report import format_text_table, write_processing_report
from src.database import DB_PATH, apply_schema, connect_database


@dataclass(frozen=True)
class QualityCheckResult:
    """Summary of business-quality checks on latest active visit state."""

    active_visits: int
    affected_visits: int
    issue_rows: int
    reportable_visits: int



def _format_utc(value: datetime | None) -> str:
    """Render a timestamp in an explicit, locale-independent UTC form."""
    if value is None:
        return "-"

    return (
        value.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _duration_minutes(
    started_at: datetime | None,
    ended_at: datetime | None,
) -> str:
    """Return elapsed minutes for display, including negative durations."""
    if started_at is None or ended_at is None:
        return "-"

    minutes = (ended_at - started_at).total_seconds() / 60
    return f"{minutes:.1f}"


def check_visit_quality(
    *,
    db_path: Path | str | None = None,
) -> QualityCheckResult:
    """
    Rebuild business-quality issues from the latest active visit state.

    Technical validity was established earlier. This stage checks business rules:
    known property, known contractor and non-negative completed duration.
    """
    connection = connect_database(db_path=db_path)
    apply_schema(connection)

    try:
        current_state_count = connection.execute(
            "SELECT COUNT(*) FROM visit_current_state"
        ).fetchone()[0]

        if current_state_count == 0:
            raise RuntimeError(
                "No current visit state found. "
                "Run: python -m src.process_visit_state"
            )

        connection.execute("BEGIN TRANSACTION")

        connection.execute("DELETE FROM visit_quality_issues")

        # Unknown property reference.
        connection.execute(
            """
            INSERT INTO visit_quality_issues (
                visit_id,
                issue_code,
                winning_event_id,
                source_batch_id,
                source_line_number,
                property_id,
                contractor_id,
                started_at,
                ended_at,
                issue_detail
            )
            SELECT
                s.visit_id,
                'unknown_property',
                s.winning_event_id,
                s.source_batch_id,
                s.source_line_number,
                s.property_id,
                s.contractor_id,
                s.started_at,
                s.ended_at,
                'property_id does not exist in src_properties'
            FROM visit_current_state AS s
            LEFT JOIN src_properties AS p
                ON p.property_id = s.property_id
            WHERE s.is_deleted = FALSE
              AND p.property_id IS NULL
            """
        )

        # Unknown contractor reference.
        connection.execute(
            """
            INSERT INTO visit_quality_issues (
                visit_id,
                issue_code,
                winning_event_id,
                source_batch_id,
                source_line_number,
                property_id,
                contractor_id,
                started_at,
                ended_at,
                issue_detail
            )
            SELECT
                s.visit_id,
                'unknown_contractor',
                s.winning_event_id,
                s.source_batch_id,
                s.source_line_number,
                s.property_id,
                s.contractor_id,
                s.started_at,
                s.ended_at,
                'contractor_id does not exist in src_contractors'
            FROM visit_current_state AS s
            LEFT JOIN src_contractors AS c
                ON c.contractor_id = s.contractor_id
            WHERE s.is_deleted = FALSE
              AND c.contractor_id IS NULL
            """
        )

        # Invalid completed duration. Null ended_at is a valid unfinished visit.
        connection.execute(
            """
            INSERT INTO visit_quality_issues (
                visit_id,
                issue_code,
                winning_event_id,
                source_batch_id,
                source_line_number,
                property_id,
                contractor_id,
                started_at,
                ended_at,
                issue_detail
            )
            SELECT
                s.visit_id,
                'end_before_start',
                s.winning_event_id,
                s.source_batch_id,
                s.source_line_number,
                s.property_id,
                s.contractor_id,
                s.started_at,
                s.ended_at,
                'ended_at is earlier than started_at'
            FROM visit_current_state AS s
            WHERE s.is_deleted = FALSE
              AND s.ended_at IS NOT NULL
              AND s.ended_at < s.started_at
            """
        )

        connection.execute(
            """
            UPDATE ingestion_batches
            SET business_quality_issues = 0
            """
        )

        connection.execute(
            """
            UPDATE ingestion_batches AS b
            SET business_quality_issues = q.issue_count
            FROM (
                SELECT
                    source_batch_id,
                    COUNT(*) AS issue_count
                FROM visit_quality_issues
                GROUP BY source_batch_id
            ) AS q
            WHERE b.batch_id = q.source_batch_id
            """
        )

        connection.execute("COMMIT")

        active_visits = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_current_state
            WHERE is_deleted = FALSE
            """
        ).fetchone()[0]

        affected_visits = connection.execute(
            """
            SELECT COUNT(DISTINCT visit_id)
            FROM visit_quality_issues
            """
        ).fetchone()[0]

        issue_rows = connection.execute(
            "SELECT COUNT(*) FROM visit_quality_issues"
        ).fetchone()[0]

        reportable_visits = connection.execute(
            "SELECT COUNT(*) FROM reportable_current_visits"
        ).fetchone()[0]

        return QualityCheckResult(
            active_visits=int(active_visits),
            affected_visits=int(affected_visits),
            issue_rows=int(issue_rows),
            reportable_visits=int(reportable_visits),
        )

    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise

    finally:
        connection.close()


def _quality_issue_rows(
    connection: duckdb.DuckDBPyConnection,
) -> list[tuple[object, ...]]:
    return connection.execute(
        """
        SELECT
            q.visit_id,
            q.issue_code,
            q.winning_event_id,
            q.property_id,
            q.contractor_id,
            q.started_at,
            q.ended_at,
            q.source_batch_id,
            q.source_line_number,
            q.issue_detail
        FROM visit_quality_issues AS q
        ORDER BY q.visit_id, q.issue_code
        """
    ).fetchall()


def main() -> None:
    result = check_visit_quality()

    connection = connect_database(read_only=True)
    try:
        issues = _quality_issue_rows(connection)
        deleted_visits = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_current_state
            WHERE is_deleted = TRUE
            """
        ).fetchone()[0]

        unfinished_reportable = connection.execute(
            """
            SELECT COUNT(*)
            FROM reportable_current_visits
            WHERE ended_at IS NULL
            """
        ).fetchone()[0]

        report_path = write_processing_report(connection)
    finally:
        connection.close()

    print("=" * 118)
    print("CURRENT VISIT BUSINESS-QUALITY CHECK")
    print("=" * 118)

    print(
        format_text_table(
            headers=[
                "Visit",
                "Issue",
                "Started UTC",
                "Ended UTC",
                "Duration min",
                "Property",
                "Contractor",
                "Source",
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
                    f"{batch_id}:{line_number}",
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
                    batch_id,
                    line_number,
                    detail,
                ) in issues
            ],
            widths=[8, 20, 20, 20, 12, 12, 12, 18, 42],
        )
    )

    print()
    print("OVERALL TOTALS")
    print("-" * 118)
    print(f"Current active visits              : {result.active_visits}")
    print(f"Business-quality issue rows        : {result.issue_rows}")
    print(f"Active visits affected             : {result.affected_visits}")
    print(f"Reportable active visits           : {result.reportable_visits}")
    print(f"Deleted visit tombstones           : {deleted_visits}")
    print(f"Valid unfinished reportable visits : {unfinished_reportable}")

    print()
    print(
        "Rule: deleted visits are not quality failures; unfinished visits with "
        "ended_at = NULL are valid."
    )
    print(
        "Rule: visits with business-quality issues are preserved for audit but "
        "excluded from reportable_current_visits."
    )
    print(f"Cumulative audit report: {report_path}")
    print("SUCCESS: Current-visit business-quality checks are complete.")


if __name__ == "__main__":
    main()
