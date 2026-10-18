from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.audit_report import format_text_table, write_processing_report
from src.database import apply_schema, connect_database


@dataclass(frozen=True)
class AnalyticalFactsResult:
    """Summary of Stage 8 analytical fact construction."""

    visit_facts: int
    incident_facts: int
    requested_visit_facts: int
    requested_incident_facts: int
    outside_requested_visits: int
    outside_requested_incidents: int


def _format_utc(value: datetime | None) -> str:
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


def build_analytical_facts(
    *,
    db_path: Path | str | None = None,
) -> AnalyticalFactsResult:
    """
    Rebuild visit and incident analytical facts.

    Visits use started_at for both historical company attribution and local week.
    Incidents use reported_at. Duration is elapsed TIMESTAMPTZ/UTC time.
    """
    connection = connect_database(db_path=db_path)
    apply_schema(connection)

    try:
        reportable_visit_count = connection.execute(
            "SELECT COUNT(*) FROM reportable_current_visits"
        ).fetchone()[0]

        if reportable_visit_count == 0:
            raise RuntimeError(
                "No reportable current visits found. "
                "Run Stage 6 before Stage 8."
            )

        management_interval_count = connection.execute(
            "SELECT COUNT(*) FROM property_management_intervals"
        ).fetchone()[0]

        if management_interval_count == 0:
            raise RuntimeError(
                "No property-management intervals found. "
                "Run Stage 7 before Stage 8."
            )

        missing_visit_management = connection.execute(
            """
            SELECT COUNT(*)
            FROM reportable_current_visits AS v
            LEFT JOIN property_management_intervals AS m
                ON m.property_id = v.property_id
               AND v.started_at >= m.valid_from
               AND (m.valid_to IS NULL OR v.started_at < m.valid_to)
            WHERE m.property_id IS NULL
            """
        ).fetchone()[0]

        if missing_visit_management:
            raise ValueError(
                f"{missing_visit_management} reportable visits have no "
                "historical management match"
            )

        missing_incident_management = connection.execute(
            """
            SELECT COUNT(*)
            FROM src_incidents AS i
            LEFT JOIN property_management_intervals AS m
                ON m.property_id = i.property_id
               AND i.reported_at >= m.valid_from
               AND (m.valid_to IS NULL OR i.reported_at < m.valid_to)
            WHERE m.property_id IS NULL
            """
        ).fetchone()[0]

        if missing_incident_management:
            raise ValueError(
                f"{missing_incident_management} incidents have no "
                "historical management match"
            )

        connection.execute("BEGIN TRANSACTION")
        connection.execute("DELETE FROM fact_visits")
        connection.execute("DELETE FROM fact_incidents")

        connection.execute(
            """
            INSERT INTO fact_visits (
                visit_id,
                property_id,
                property_name,
                property_timezone,
                contractor_id,
                contractor_name,
                visitor_id,
                company_id,
                company_name,
                started_at,
                ended_at,
                local_started_at,
                local_ended_at,
                week_start_local,
                is_requested_week,
                is_unfinished,
                completed_hours,
                source_version,
                winning_event_id
            )
            WITH localized AS (
                SELECT
                    v.visit_id,
                    v.property_id,
                    p.property_name,
                    p.timezone AS property_timezone,
                    v.contractor_id,
                    k.contractor_name,
                    v.visitor_id,
                    m.company_id,
                    c.company_name,
                    v.started_at,
                    v.ended_at,
                    timezone(p.timezone, v.started_at) AS local_started_at,
                    CASE
                        WHEN v.ended_at IS NULL THEN NULL
                        ELSE timezone(p.timezone, v.ended_at)
                    END AS local_ended_at,
                    v.source_version,
                    v.winning_event_id
                FROM reportable_current_visits AS v
                JOIN src_properties AS p
                    ON p.property_id = v.property_id
                JOIN src_contractors AS k
                    ON k.contractor_id = v.contractor_id
                JOIN property_management_intervals AS m
                    ON m.property_id = v.property_id
                   AND v.started_at >= m.valid_from
                   AND (m.valid_to IS NULL OR v.started_at < m.valid_to)
                JOIN src_companies AS c
                    ON c.company_id = m.company_id
            ),
            weeked AS (
                SELECT
                    *,
                    CAST(
                        date_trunc('week', local_started_at)
                        AS DATE
                    ) AS week_start_local
                FROM localized
            )
            SELECT
                w.visit_id,
                w.property_id,
                w.property_name,
                w.property_timezone,
                w.contractor_id,
                w.contractor_name,
                w.visitor_id,
                w.company_id,
                w.company_name,
                w.started_at,
                w.ended_at,
                w.local_started_at,
                w.local_ended_at,
                w.week_start_local,
                rw.week_start IS NOT NULL AS is_requested_week,
                w.ended_at IS NULL AS is_unfinished,
                CASE
                    WHEN w.ended_at IS NULL THEN NULL
                    ELSE epoch(w.ended_at - w.started_at) / 3600.0
                END AS completed_hours,
                w.source_version,
                w.winning_event_id
            FROM weeked AS w
            LEFT JOIN src_report_weeks AS rw
                ON rw.week_start = w.week_start_local
            ORDER BY w.visit_id
            """
        )

        connection.execute(
            """
            INSERT INTO fact_incidents (
                incident_id,
                property_id,
                property_name,
                property_timezone,
                company_id,
                company_name,
                reported_at,
                local_reported_at,
                week_start_local,
                is_requested_week,
                severity,
                is_high
            )
            WITH localized AS (
                SELECT
                    i.incident_id,
                    i.property_id,
                    p.property_name,
                    p.timezone AS property_timezone,
                    m.company_id,
                    c.company_name,
                    i.reported_at,
                    timezone(p.timezone, i.reported_at) AS local_reported_at,
                    i.severity
                FROM src_incidents AS i
                JOIN src_properties AS p
                    ON p.property_id = i.property_id
                JOIN property_management_intervals AS m
                    ON m.property_id = i.property_id
                   AND i.reported_at >= m.valid_from
                   AND (m.valid_to IS NULL OR i.reported_at < m.valid_to)
                JOIN src_companies AS c
                    ON c.company_id = m.company_id
            ),
            weeked AS (
                SELECT
                    *,
                    CAST(
                        date_trunc('week', local_reported_at)
                        AS DATE
                    ) AS week_start_local
                FROM localized
            )
            SELECT
                w.incident_id,
                w.property_id,
                w.property_name,
                w.property_timezone,
                w.company_id,
                w.company_name,
                w.reported_at,
                w.local_reported_at,
                w.week_start_local,
                rw.week_start IS NOT NULL AS is_requested_week,
                w.severity,
                w.severity = 'high' AS is_high
            FROM weeked AS w
            LEFT JOIN src_report_weeks AS rw
                ON rw.week_start = w.week_start_local
            ORDER BY w.incident_id
            """
        )

        visit_facts = connection.execute(
            "SELECT COUNT(*) FROM fact_visits"
        ).fetchone()[0]

        incident_facts = connection.execute(
            "SELECT COUNT(*) FROM fact_incidents"
        ).fetchone()[0]

        source_incidents = connection.execute(
            "SELECT COUNT(*) FROM src_incidents"
        ).fetchone()[0]

        if visit_facts != reportable_visit_count:
            raise ValueError(
                "Visit fact grain changed during historical attribution: "
                f"expected {reportable_visit_count}, got {visit_facts}"
            )

        if incident_facts != source_incidents:
            raise ValueError(
                "Incident fact grain changed during historical attribution: "
                f"expected {source_incidents}, got {incident_facts}"
            )

        requested_visits = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_visits
            WHERE is_requested_week = TRUE
            """
        ).fetchone()[0]

        requested_incidents = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_incidents
            WHERE is_requested_week = TRUE
            """
        ).fetchone()[0]

        if requested_visits == 0 or requested_incidents == 0:
            raise ValueError(
                "No facts matched src_report_weeks. "
                "Check DuckDB calendar/timezone session settings and local-week derivation."
            )

        connection.execute("COMMIT")

        return AnalyticalFactsResult(
            visit_facts=int(visit_facts),
            incident_facts=int(incident_facts),
            requested_visit_facts=int(requested_visits),
            requested_incident_facts=int(requested_incidents),
            outside_requested_visits=int(visit_facts - requested_visits),
            outside_requested_incidents=int(
                incident_facts - requested_incidents
            ),
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
    result = build_analytical_facts()

    connection = connect_database(read_only=True)
    try:
        requested_metrics = connection.execute(
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

        incident_metrics = connection.execute(
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

        report_path = write_processing_report(connection)
    finally:
        connection.close()

    print("=" * 118)
    print("ANALYTICAL VISIT AND INCIDENT FACTS")
    print("=" * 118)

    print("FACT COUNTS")
    print("-" * 118)
    print(f"Visit facts (all reportable current visits) : {result.visit_facts}")
    print(f"Incident facts (all supplied incidents)     : {result.incident_facts}")
    print(f"Visits in requested local weeks             : {result.requested_visit_facts}")
    print(f"Incidents in requested local weeks          : {result.requested_incident_facts}")
    print(f"Visits outside requested weeks              : {result.outside_requested_visits}")
    print(f"Incidents outside requested weeks           : {result.outside_requested_incidents}")

    print()
    print("REQUESTED-WEEK METRICS")
    print("-" * 118)
    print(f"Visit count                : {requested_metrics[0]}")
    print(f"Distinct visitors          : {requested_metrics[1]}")
    print(f"Completed visit hours      : {float(requested_metrics[2]):.2f}")
    print(f"Unfinished visits          : {requested_metrics[3]}")
    print(f"Incident count             : {incident_metrics[0]}")
    print(f"High-severity incidents    : {incident_metrics[1]}")

    print()
    print("HISTORICAL COMPANY ATTRIBUTION PROOF")
    print("-" * 118)
    print(
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
        )
    )

    print()
    print("PROPERTY-LOCAL WEEK BOUNDARY PROOF")
    print("-" * 118)
    print(
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
        )
    )

    print()
    print("DST / ELAPSED-HOURS PROOF")
    print("-" * 118)
    print(
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
        )
    )

    print()
    print(
        "Rule: visit company and local week use started_at; incident company "
        "and local week use reported_at."
    )
    print(
        "Rule: completed hours use elapsed TIMESTAMPTZ/UTC time, so DST does "
        "not distort duration."
    )
    print(
        "Rule: visits and incidents remain separate fact grains; they are not "
        "raw-joined before aggregation."
    )
    print(f"Cumulative audit report: {report_path}")
    print("SUCCESS: Analytical visit and incident facts are complete.")


if __name__ == "__main__":
    main()
