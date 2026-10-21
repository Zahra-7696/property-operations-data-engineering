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
                COUNT(*),
                COUNT(DISTINCT visitor_id),
                ROUND(SUM(COALESCE(completed_hours, 0)), 2),
                SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END)
            FROM fact_visits
            WHERE is_requested_week = TRUE
            """
        ).fetchone()

        incident_metrics = connection.execute(
            """
            SELECT COUNT(*), SUM(CASE WHEN is_high THEN 1 ELSE 0 END)
            FROM fact_incidents
            WHERE is_requested_week = TRUE
            """
        ).fetchone()

        history_examples = connection.execute(
            """
            SELECT 'visit', visit_id, property_id, started_at, company_id, company_name
            FROM fact_visits
            WHERE visit_id IN ('SYN_V00373', 'SYN_V01461')
            UNION ALL
            SELECT 'incident', incident_id, property_id, reported_at, company_id, company_name
            FROM fact_incidents
            WHERE incident_id = 'SYN_I0153'
            ORDER BY 4, 1, 2
            """
        ).fetchall()

        week_examples = connection.execute(
            """
            SELECT visit_id, property_id, property_timezone, started_at,
                   local_started_at, week_start_local
            FROM fact_visits
            WHERE visit_id IN ('V14', 'V01')
            ORDER BY started_at
            """
        ).fetchall()

        all_week_shifts = connection.execute(
            """
            SELECT
                visit_id, property_id, property_name, property_timezone,
                started_at, local_started_at,
                CAST(DATE_TRUNC('week', started_at AT TIME ZONE 'UTC') AS DATE),
                week_start_local
            FROM fact_visits
            WHERE CAST(DATE_TRUNC('week', started_at AT TIME ZONE 'UTC') AS DATE)
                  <> week_start_local
            ORDER BY started_at, visit_id
            """
        ).fetchall()

        dst_examples = connection.execute(
            """
            SELECT
                visit_id, company_id, property_id, property_timezone,
                started_at, ended_at, local_started_at, local_ended_at,
                is_unfinished, completed_hours
            FROM fact_visits
            WHERE visit_id IN ('V07', 'V16')
            ORDER BY visit_id
            """
        ).fetchall()

        completion_summary = connection.execute(
            """
            SELECT
                company_id, company_name, property_id, property_name,
                COUNT(*),
                SUM(CASE WHEN NOT is_unfinished THEN 1 ELSE 0 END),
                SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END),
                ROUND(SUM(COALESCE(completed_hours, 0)), 2)
            FROM fact_visits
            GROUP BY company_id, company_name, property_id, property_name
            ORDER BY company_id, property_id
            """
        ).fetchall()

        unfinished_details = connection.execute(
            """
            SELECT visit_id, company_id, property_id, started_at,
                   local_started_at, week_start_local, is_requested_week
            FROM fact_visits
            WHERE is_unfinished = TRUE
            ORDER BY company_id, property_id, started_at, visit_id
            """
        ).fetchall()

        outside_visits = connection.execute(
            """
            SELECT visit_id, company_id, property_id, local_started_at,
                   week_start_local, is_unfinished, completed_hours
            FROM fact_visits
            WHERE is_requested_week = FALSE
            ORDER BY week_start_local, local_started_at, visit_id
            """
        ).fetchall()

        outside_incidents = connection.execute(
            """
            SELECT incident_id, company_id, property_id, property_timezone,
                   reported_at, local_reported_at, week_start_local, severity, is_high
            FROM fact_incidents
            WHERE is_requested_week = FALSE
            ORDER BY week_start_local, local_reported_at, incident_id
            """
        ).fetchall()

        sample_visits = connection.execute(
            """
            SELECT visit_id, company_id, property_id, local_started_at,
                   is_unfinished, completed_hours
            FROM fact_visits
            WHERE CAST(local_started_at AS DATE) = DATE '2026-03-23'
            ORDER BY local_started_at, visit_id
            """
        ).fetchall()

        sample_incidents = connection.execute(
            """
            SELECT incident_id, company_id, property_id, local_reported_at, severity
            FROM fact_incidents
            WHERE CAST(local_reported_at AS DATE) = DATE '2026-03-23'
            ORDER BY local_reported_at, incident_id
            """
        ).fetchall()

        requested_weeks = connection.execute(
            """
            SELECT week_start, week_start + INTERVAL 6 DAY
            FROM src_report_weeks
            ORDER BY week_start
            """
        ).fetchall()

        severity_summary = connection.execute(
            """
            SELECT severity, COUNT(*),
                   SUM(CASE WHEN is_high THEN 1 ELSE 0 END)
            FROM fact_incidents
            GROUP BY severity
            ORDER BY CASE severity WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'high' THEN 3 ELSE 4 END
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

    print("\nPERIOD-INCLUSION SUMMARY")
    print("-" * 118)
    print(format_text_table(
        headers=["Measure", "Count", "Meaning"],
        rows=[
            ("Valid visits outside requested weeks", result.outside_requested_visits,
             "Valid facts retained but outside this report period."),
            ("Valid incidents outside requested weeks", result.outside_requested_incidents,
             "Valid facts retained but outside this report period."),
        ],
        widths=[43, 8, 61],
    ))

    print("\nREQUESTED REPORT WEEKS")
    print("-" * 118)
    print(format_text_table(
        headers=["Local week start", "Local week end", "Meaning"],
        rows=[(start, end, "Requested local Monday week") for start, end in requested_weeks],
        widths=[18, 20, 60],
    ))
    print("A fact is in-period when its derived property-local Monday week exists in src_report_weeks.")

    print("\nINCIDENT SEVERITY SUMMARY")
    print("-" * 118)
    print(format_text_table(
        headers=["Severity", "Incidents", "High-flag rows", "Rule"],
        rows=[
            (severity, count, high_count,
             "is_high = TRUE" if severity == "high" else "is_high = FALSE")
            for severity, count, high_count in severity_summary
        ],
        widths=[12, 10, 15, 28],
    ))
    print("Severity is source-provided: low, medium or high. is_high is TRUE only when severity='high'.")

    print("\nREQUESTED-WEEK METRICS")
    print("-" * 118)
    print(f"Visit count                : {requested_metrics[0]}")
    print(f"Distinct visitors          : {requested_metrics[1]}")
    print(f"Completed visit hours      : {float(requested_metrics[2]):.2f}")
    print(f"Unfinished visits          : {requested_metrics[3]}")
    print(f"Incident count             : {incident_metrics[0]}")
    print(f"High-severity incidents    : {incident_metrics[1]}")

    print("\nP11 HISTORICAL COMPANY ATTRIBUTION PROOF")
    print("-" * 118)
    print(format_text_table(
        headers=["Type", "Record", "Property", "Business time UTC", "Company", "Company name"],
        rows=[(t, r, p, _format_utc(bt), c, cn) for t, r, p, bt, c, cn in history_examples],
        widths=[10, 14, 10, 22, 10, 24],
    ))

    print("\nP01 PROPERTY-LOCAL WEEK BOUNDARY PROOF")
    print("-" * 118)
    print(format_text_table(
        headers=["Visit", "Property", "Timezone", "Started UTC", "Local start", "Local week"],
        rows=[(v, p, tz, _format_utc(s), _format_local(ls), w) for v, p, tz, s, ls, w in week_examples],
        widths=[8, 10, 20, 22, 20, 12],
    ))

    print("\nALL UTC-VS-PROPERTY-LOCAL WEEK SHIFTS")
    print("-" * 118)
    print(format_text_table(
        headers=["Visit", "Property", "Property name", "Timezone", "Started UTC", "Local start", "UTC week", "Local week"],
        rows=[(v, p, pn, tz, _format_utc(s), _format_local(ls), uw, lw) for v, p, pn, tz, s, ls, uw, lw in all_week_shifts],
        widths=[13, 9, 22, 20, 22, 20, 12, 12],
    ))

    print("\nDST / ELAPSED-HOURS PROOF WITH COMPANY/PROPERTY")
    print("-" * 118)
    print(format_text_table(
        headers=["Visit", "Company", "Property", "Timezone", "Start UTC", "End UTC", "Local start", "Local end", "Unfinished", "Hours"],
        rows=[(
            v, c, p, tz, _format_utc(s), _format_utc(e), _format_local(ls), _format_local(le),
            str(u), "N/A" if h is None else f"{h:.2f}"
        ) for v, c, p, tz, s, e, ls, le, u, h in dst_examples],
        widths=[8, 8, 9, 20, 22, 22, 20, 20, 10, 8],
    ))

    print("\nVISIT COMPLETION SUMMARY BY COMPANY / PROPERTY")
    print("-" * 118)
    print(format_text_table(
        headers=["Company", "Company name", "Property", "Property name", "Visits", "Completed", "Unfinished", "Completed hours"],
        rows=[(c, cn, p, pn, n, comp, unfin, f"{hours:.2f}") for c, cn, p, pn, n, comp, unfin, hours in completion_summary],
        widths=[8, 24, 9, 22, 8, 10, 10, 15],
    ))

    print("\nUNFINISHED VISIT DETAILS")
    print("-" * 118)
    print(format_text_table(
        headers=["Visit", "Company", "Property", "Started UTC", "Local start", "Local week", "Requested"],
        rows=[(v, c, p, _format_utc(s), _format_local(ls), w, str(req)) for v, c, p, s, ls, w, req in unfinished_details],
        widths=[13, 8, 9, 22, 20, 12, 10],
    ))
    print("Unfinished duration is intentionally not calculated because ended_at is NULL.")

    print("\nSAMPLE LOCAL-DATE FILTER — 2026-03-23 VISITS")
    print("-" * 118)
    print(format_text_table(
        headers=["Visit", "Company", "Property", "Local start", "Unfinished", "Hours"],
        rows=[(v, c, p, _format_local(ls), str(u), "N/A" if h is None else f"{h:.2f}") for v, c, p, ls, u, h in sample_visits],
        widths=[13, 8, 9, 20, 10, 8],
    ))

    print("\nSAMPLE LOCAL-DATE FILTER — 2026-03-23 INCIDENTS")
    print("-" * 118)
    print(format_text_table(
        headers=["Incident", "Company", "Property", "Local reported", "Severity"],
        rows=[(i, c, p, _format_local(lr), sev) for i, c, p, lr, sev in sample_incidents],
        widths=[13, 8, 9, 20, 10],
    ))

    print("\nVISITS OUTSIDE REQUESTED REPORT WEEKS")
    print("-" * 118)
    print(format_text_table(
        headers=["Visit", "Company", "Property", "Local start", "Local week", "Unfinished", "Hours"],
        rows=[(v, c, p, _format_local(ls), w, str(u), "N/A" if h is None else f"{h:.2f}") for v, c, p, ls, w, u, h in outside_visits],
        widths=[13, 8, 9, 20, 12, 10, 8],
    ))

    print("\nINCIDENTS OUTSIDE REQUESTED REPORT WEEKS")
    print("-" * 118)
    print(format_text_table(
        headers=["Incident", "Company", "Property", "Timezone", "Reported UTC", "Local reported", "Local week", "Severity", "High"],
        rows=[
            (i, c, p, tz, _format_utc(ru), _format_local(lr), w, sev, str(high))
            for i, c, p, tz, ru, lr, w, sev, high in outside_incidents
        ],
        widths=[13, 8, 9, 20, 22, 20, 12, 10, 7],
    ))
    print("The local week, not the UTC date, determines requested-period inclusion.")

    print("\nRule: visit company/local week use started_at; incident company/local week use reported_at.")
    print("Rule: completed hours use elapsed TIMESTAMPTZ/UTC time, so DST does not distort duration.")
    print("Rule: unfinished visits have no ended_at, so their duration is unknown and is never invented.")
    print("Rule: visits and incidents remain separate fact grains; they are not raw-joined before aggregation.")
    print(f"Cumulative audit report: {report_path}")
    print("SUCCESS: Analytical visit and incident facts are complete.")


if __name__ == "__main__":
    main()
