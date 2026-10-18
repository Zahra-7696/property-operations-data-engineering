from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from src.audit_report import format_text_table, write_processing_report
from src.database import apply_schema, connect_database


@dataclass(frozen=True)
class ReportingMartsResult:
    """Summary of Stage 9 reporting-mart construction."""

    company_week_rows: int
    company_period_rows: int
    property_week_rows: int
    contractor_rows: int
    zero_activity_property_weeks: int


def build_reporting_marts(
    *,
    db_path: Path | str | None = None,
) -> ReportingMartsResult:
    """
    Rebuild requested-period reporting marts from Stage 8 fact tables.

    Visits and incidents are aggregated independently before they are combined.
    Distinct visitors are always calculated at the target display grain.
    """
    connection = connect_database(db_path=db_path)
    apply_schema(connection)

    try:
        visit_fact_count = connection.execute(
            "SELECT COUNT(*) FROM fact_visits"
        ).fetchone()[0]

        incident_fact_count = connection.execute(
            "SELECT COUNT(*) FROM fact_incidents"
        ).fetchone()[0]

        if visit_fact_count == 0 or incident_fact_count == 0:
            raise RuntimeError(
                "Stage 8 fact tables are empty. "
                "Run: python -m src.build_analytical_facts"
            )

        connection.execute("BEGIN TRANSACTION")

        connection.execute("DELETE FROM rpt_company_week")
        connection.execute("DELETE FROM rpt_company_period")
        connection.execute("DELETE FROM rpt_property_week")
        connection.execute("DELETE FROM rpt_contractor")

        # Company/week trend. The scaffold keeps every requested company/week.
        connection.execute(
            """
            INSERT INTO rpt_company_week (
                company_id,
                company_name,
                week_start,
                visit_count,
                distinct_visitors,
                completed_visit_hours,
                unfinished_visits,
                incident_count,
                high_severity_incidents
            )
            WITH scaffold AS (
                SELECT
                    c.company_id,
                    c.company_name,
                    w.week_start
                FROM src_companies AS c
                CROSS JOIN src_report_weeks AS w
            ),
            visit_metrics AS (
                SELECT
                    company_id,
                    week_start_local AS week_start,
                    COUNT(*) AS visit_count,
                    COUNT(DISTINCT visitor_id) AS distinct_visitors,
                    ROUND(COALESCE(SUM(completed_hours), 0), 2)
                        AS completed_visit_hours,
                    SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END)
                        AS unfinished_visits
                FROM fact_visits
                WHERE is_requested_week = TRUE
                GROUP BY company_id, week_start_local
            ),
            incident_metrics AS (
                SELECT
                    company_id,
                    week_start_local AS week_start,
                    COUNT(*) AS incident_count,
                    SUM(CASE WHEN is_high THEN 1 ELSE 0 END)
                        AS high_severity_incidents
                FROM fact_incidents
                WHERE is_requested_week = TRUE
                GROUP BY company_id, week_start_local
            )
            SELECT
                s.company_id,
                s.company_name,
                s.week_start,
                COALESCE(v.visit_count, 0),
                COALESCE(v.distinct_visitors, 0),
                COALESCE(v.completed_visit_hours, 0),
                COALESCE(v.unfinished_visits, 0),
                COALESCE(i.incident_count, 0),
                COALESCE(i.high_severity_incidents, 0)
            FROM scaffold AS s
            LEFT JOIN visit_metrics AS v
                ON v.company_id = s.company_id
               AND v.week_start = s.week_start
            LEFT JOIN incident_metrics AS i
                ON i.company_id = s.company_id
               AND i.week_start = s.week_start
            ORDER BY s.week_start, s.company_id
            """
        )

        # Company requested-period totals. Distinct visitors are recomputed
        # directly at this grain rather than summed from weekly rows.
        connection.execute(
            """
            INSERT INTO rpt_company_period (
                company_id,
                company_name,
                visit_count,
                distinct_visitors,
                completed_visit_hours,
                unfinished_visits,
                incident_count,
                high_severity_incidents
            )
            WITH visit_metrics AS (
                SELECT
                    company_id,
                    COUNT(*) AS visit_count,
                    COUNT(DISTINCT visitor_id) AS distinct_visitors,
                    ROUND(COALESCE(SUM(completed_hours), 0), 2)
                        AS completed_visit_hours,
                    SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END)
                        AS unfinished_visits
                FROM fact_visits
                WHERE is_requested_week = TRUE
                GROUP BY company_id
            ),
            incident_metrics AS (
                SELECT
                    company_id,
                    COUNT(*) AS incident_count,
                    SUM(CASE WHEN is_high THEN 1 ELSE 0 END)
                        AS high_severity_incidents
                FROM fact_incidents
                WHERE is_requested_week = TRUE
                GROUP BY company_id
            )
            SELECT
                c.company_id,
                c.company_name,
                COALESCE(v.visit_count, 0),
                COALESCE(v.distinct_visitors, 0),
                COALESCE(v.completed_visit_hours, 0),
                COALESCE(v.unfinished_visits, 0),
                COALESCE(i.incident_count, 0),
                COALESCE(i.high_severity_incidents, 0)
            FROM src_companies AS c
            LEFT JOIN visit_metrics AS v
                ON v.company_id = c.company_id
            LEFT JOIN incident_metrics AS i
                ON i.company_id = c.company_id
            ORDER BY c.company_id
            """
        )

        # Property/week summary: 24 properties x 6 requested weeks = 144 rows.
        connection.execute(
            """
            INSERT INTO rpt_property_week (
                property_id,
                property_name,
                week_start,
                visit_count,
                distinct_visitors,
                completed_visit_hours,
                unfinished_visits,
                incident_count,
                high_severity_incidents,
                has_activity
            )
            WITH scaffold AS (
                SELECT
                    p.property_id,
                    p.property_name,
                    w.week_start
                FROM src_properties AS p
                CROSS JOIN src_report_weeks AS w
            ),
            visit_metrics AS (
                SELECT
                    property_id,
                    week_start_local AS week_start,
                    COUNT(*) AS visit_count,
                    COUNT(DISTINCT visitor_id) AS distinct_visitors,
                    ROUND(COALESCE(SUM(completed_hours), 0), 2)
                        AS completed_visit_hours,
                    SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END)
                        AS unfinished_visits
                FROM fact_visits
                WHERE is_requested_week = TRUE
                GROUP BY property_id, week_start_local
            ),
            incident_metrics AS (
                SELECT
                    property_id,
                    week_start_local AS week_start,
                    COUNT(*) AS incident_count,
                    SUM(CASE WHEN is_high THEN 1 ELSE 0 END)
                        AS high_severity_incidents
                FROM fact_incidents
                WHERE is_requested_week = TRUE
                GROUP BY property_id, week_start_local
            )
            SELECT
                s.property_id,
                s.property_name,
                s.week_start,
                COALESCE(v.visit_count, 0),
                COALESCE(v.distinct_visitors, 0),
                COALESCE(v.completed_visit_hours, 0),
                COALESCE(v.unfinished_visits, 0),
                COALESCE(i.incident_count, 0),
                COALESCE(i.high_severity_incidents, 0),
                (
                    COALESCE(v.visit_count, 0)
                    + COALESCE(i.incident_count, 0)
                ) > 0 AS has_activity
            FROM scaffold AS s
            LEFT JOIN visit_metrics AS v
                ON v.property_id = s.property_id
               AND v.week_start = s.week_start
            LEFT JOIN incident_metrics AS i
                ON i.property_id = s.property_id
               AND i.week_start = s.week_start
            ORDER BY s.property_id, s.week_start
            """
        )

        # Contractor requested-period breakdown. Incidents have no contractor key.
        connection.execute(
            """
            INSERT INTO rpt_contractor (
                contractor_id,
                contractor_name,
                visit_count,
                distinct_visitors,
                property_count,
                completed_visit_hours,
                unfinished_visits
            )
            WITH metrics AS (
                SELECT
                    contractor_id,
                    COUNT(*) AS visit_count,
                    COUNT(DISTINCT visitor_id) AS distinct_visitors,
                    COUNT(DISTINCT property_id) AS property_count,
                    ROUND(COALESCE(SUM(completed_hours), 0), 2)
                        AS completed_visit_hours,
                    SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END)
                        AS unfinished_visits
                FROM fact_visits
                WHERE is_requested_week = TRUE
                GROUP BY contractor_id
            )
            SELECT
                c.contractor_id,
                c.contractor_name,
                COALESCE(m.visit_count, 0),
                COALESCE(m.distinct_visitors, 0),
                COALESCE(m.property_count, 0),
                COALESCE(m.completed_visit_hours, 0),
                COALESCE(m.unfinished_visits, 0)
            FROM src_contractors AS c
            LEFT JOIN metrics AS m
                ON m.contractor_id = c.contractor_id
            ORDER BY c.contractor_id
            """
        )

        company_week_rows = connection.execute(
            "SELECT COUNT(*) FROM rpt_company_week"
        ).fetchone()[0]

        company_period_rows = connection.execute(
            "SELECT COUNT(*) FROM rpt_company_period"
        ).fetchone()[0]

        property_week_rows = connection.execute(
            "SELECT COUNT(*) FROM rpt_property_week"
        ).fetchone()[0]

        contractor_rows = connection.execute(
            "SELECT COUNT(*) FROM rpt_contractor"
        ).fetchone()[0]

        zero_property_weeks = connection.execute(
            """
            SELECT COUNT(*)
            FROM rpt_property_week
            WHERE has_activity = FALSE
            """
        ).fetchone()[0]

        requested_visit_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_visits
            WHERE is_requested_week = TRUE
            """
        ).fetchone()[0]

        requested_incident_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_incidents
            WHERE is_requested_week = TRUE
            """
        ).fetchone()[0]

        mart_visit_count = connection.execute(
            "SELECT SUM(visit_count) FROM rpt_company_period"
        ).fetchone()[0]

        mart_incident_count = connection.execute(
            "SELECT SUM(incident_count) FROM rpt_company_period"
        ).fetchone()[0]

        if company_week_rows != 18:
            raise ValueError(
                f"Expected 18 company/week rows, got {company_week_rows}"
            )

        if company_period_rows != 3:
            raise ValueError(
                f"Expected 3 company-period rows, got {company_period_rows}"
            )

        if property_week_rows != 144:
            raise ValueError(
                f"Expected 144 property/week rows, got {property_week_rows}"
            )

        if contractor_rows != 8:
            raise ValueError(
                f"Expected 8 contractor rows, got {contractor_rows}"
            )

        if mart_visit_count != requested_visit_count:
            raise ValueError(
                "Company-period visit totals do not reconcile to Stage 8 facts"
            )

        if mart_incident_count != requested_incident_count:
            raise ValueError(
                "Company-period incident totals do not reconcile to Stage 8 facts"
            )

        connection.execute("COMMIT")

        return ReportingMartsResult(
            company_week_rows=int(company_week_rows),
            company_period_rows=int(company_period_rows),
            property_week_rows=int(property_week_rows),
            contractor_rows=int(contractor_rows),
            zero_activity_property_weeks=int(zero_property_weeks),
        )

    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise

    finally:
        connection.close()


def main() -> None:
    result = build_reporting_marts()

    connection = connect_database(read_only=True)
    try:
        company_period = connection.execute(
            """
            SELECT
                company_name,
                visit_count,
                distinct_visitors,
                completed_visit_hours,
                unfinished_visits,
                incident_count,
                high_severity_incidents
            FROM rpt_company_period
            ORDER BY company_id
            """
        ).fetchall()

        company_week = connection.execute(
            """
            SELECT
                week_start,
                company_name,
                visit_count,
                distinct_visitors,
                completed_visit_hours,
                unfinished_visits,
                incident_count,
                high_severity_incidents
            FROM rpt_company_week
            ORDER BY week_start, company_id
            """
        ).fetchall()

        zero_property_weeks = connection.execute(
            """
            SELECT
                property_id,
                property_name,
                week_start,
                visit_count,
                incident_count
            FROM rpt_property_week
            WHERE has_activity = FALSE
            ORDER BY property_id, week_start
            """
        ).fetchall()

        contractors = connection.execute(
            """
            SELECT
                contractor_name,
                visit_count,
                distinct_visitors,
                property_count,
                completed_visit_hours,
                unfinished_visits
            FROM rpt_contractor
            ORDER BY visit_count DESC, contractor_id
            """
        ).fetchall()

        overall_distinct_visitors = connection.execute(
            """
            SELECT COUNT(DISTINCT visitor_id)
            FROM fact_visits
            WHERE is_requested_week = TRUE
            """
        ).fetchone()[0]

        summed_company_distinct = connection.execute(
            "SELECT SUM(distinct_visitors) FROM rpt_company_period"
        ).fetchone()[0]

        report_path = write_processing_report(connection)
    finally:
        connection.close()

    print("=" * 118)
    print("REPORTING MARTS")
    print("=" * 118)

    print("MART COUNTS")
    print("-" * 118)
    print(f"Company/week rows                 : {result.company_week_rows}")
    print(f"Company requested-period rows     : {result.company_period_rows}")
    print(f"Property/week rows                : {result.property_week_rows}")
    print(f"Contractor rows                   : {result.contractor_rows}")
    print(
        "Zero-activity property/week rows  : "
        f"{result.zero_activity_property_weeks}"
    )

    print()
    print("COMPANY REQUESTED-PERIOD SUMMARY")
    print("-" * 118)
    print(
        format_text_table(
            headers=[
                "Company",
                "Visits",
                "Distinct visitors",
                "Hours",
                "Unfinished",
                "Incidents",
                "High",
            ],
            rows=[
                (
                    company_name,
                    visit_count,
                    distinct_visitors,
                    f"{completed_hours:.2f}",
                    unfinished_visits,
                    incident_count,
                    high_incidents,
                )
                for (
                    company_name,
                    visit_count,
                    distinct_visitors,
                    completed_hours,
                    unfinished_visits,
                    incident_count,
                    high_incidents,
                ) in company_period
            ],
            widths=[24, 8, 18, 10, 11, 10, 8],
        )
    )

    print()
    print("COMPANY WEEKLY TREND")
    print("-" * 118)
    print(
        format_text_table(
            headers=[
                "Week",
                "Company",
                "Visits",
                "Visitors",
                "Hours",
                "Unfinished",
                "Incidents",
                "High",
            ],
            rows=[
                (
                    week_start,
                    company_name,
                    visit_count,
                    distinct_visitors,
                    f"{completed_hours:.2f}",
                    unfinished_visits,
                    incident_count,
                    high_incidents,
                )
                for (
                    week_start,
                    company_name,
                    visit_count,
                    distinct_visitors,
                    completed_hours,
                    unfinished_visits,
                    incident_count,
                    high_incidents,
                ) in company_week
            ],
            widths=[12, 24, 8, 9, 10, 11, 10, 8],
        )
    )

    print()
    print("ZERO-ACTIVITY PROPERTY WEEKS")
    print("-" * 118)
    print(
        format_text_table(
            headers=["Property", "Property name", "Week", "Visits", "Incidents"],
            rows=zero_property_weeks,
            widths=[10, 24, 12, 8, 10],
        )
    )

    print()
    print("CONTRACTOR BREAKDOWN")
    print("-" * 118)
    print(
        format_text_table(
            headers=[
                "Contractor",
                "Visits",
                "Visitors",
                "Properties",
                "Hours",
                "Unfinished",
            ],
            rows=[
                (
                    contractor_name,
                    visit_count,
                    distinct_visitors,
                    property_count,
                    f"{completed_hours:.2f}",
                    unfinished_visits,
                )
                for (
                    contractor_name,
                    visit_count,
                    distinct_visitors,
                    property_count,
                    completed_hours,
                    unfinished_visits,
                ) in contractors
            ],
            widths=[24, 8, 9, 11, 10, 11],
        )
    )

    print()
    print("DISTINCT-VISITOR NON-ADDITIVITY CHECK")
    print("-" * 118)
    print(
        "Overall requested-period distinct visitors : "
        f"{overall_distinct_visitors}"
    )
    print(
        "Sum of company distinct visitors           : "
        f"{summed_company_distinct}"
    )
    print(
        "They differ because the same visitor may appear in several companies. "
        "Higher-level distinct counts must be recomputed directly."
    )

    print()
    print(
        "Rule: visits and incidents are aggregated separately before metrics "
        "are combined."
    )
    print(
        "Rule: property/week uses a 24 x 6 scaffold so zero-activity weeks "
        "remain visible."
    )
    print(f"Cumulative audit report: {report_path}")
    print("SUCCESS: Reporting marts are complete.")


if __name__ == "__main__":
    main()
