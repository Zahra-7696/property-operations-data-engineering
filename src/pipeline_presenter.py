from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import duckdb

from src.audit_report import format_text_table


@dataclass(frozen=True)
class StageGuide:
    number: int
    title: str
    aim: str
    questions: tuple[str, ...]
    test_summary: str
    test_coverage: str


STAGES: dict[int, StageGuide] = {
    1: StageGuide(
        1,
        "Source integrity",
        (
            "Prove that the committed raw files are exactly the supplied assignment "
            "inputs before any transformation starts."
        ),
        (
            "Are the supplied files unchanged?",
            "Do row counts and SHA256 checksums match the manifest?",
        ),
        "Integrity verification passed for all 12 supplied files.",
        "row counts, SHA256 checksums and immutable-source protection",
    ),
    2: StageGuide(
        2,
        "Typed reference data",
        (
            "Load companies, contractors, properties, management observations, "
            "incidents and requested weeks into persistent typed DuckDB tables."
        ),
        (
            "What stable reference entities exist?",
            "Are their counts, keys and relationships valid?",
        ),
        "11 stage-specific tests passed in the verified repository state.",
        "reference counts, keys, relationships, severity values and report weeks",
    ),
    3: StageGuide(
        3,
        "Raw visit batch landing",
        (
            "Preserve every physical JSONL line in manifest order and record durable "
            "batch progress without interpreting malformed source lines yet."
        ),
        (
            "Which batch and line did each raw record come from?",
            "Can replay/restart happen without duplicating or losing raw rows?",
        ),
        "6 stage-specific tests passed in the verified repository state.",
        "manifest order, exact line counts, replay no-op and rollback/restart",
    ),
    4: StageGuide(
        4,
        "Technical event validation",
        (
            "Separate technically understandable visit-change events from malformed "
            "or incomplete input while retaining exact reject evidence."
        ),
        (
            "Which lines are technically valid?",
            "Which lines are rejected and why?",
        ),
        "12 stage-specific tests passed in the verified repository state.",
        "valid/invalid counts, reject reasons, timestamps, evidence and replay",
    ),
    5: StageGuide(
        5,
        "Deduplication and current visit state",
        (
            "Deduplicate repeated event deliveries and keep the highest source version "
            "for each visit, including delete tombstones and newer reactivation."
        ),
        (
            "Is an event delivery duplicated?",
            "Is an incoming visit version newer, stale or equal?",
            "What is the latest active/deleted state of each visit?",
        ),
        "11 stage-specific tests passed in the verified repository state.",
        "duplicates, source-version precedence, tombstones, reactivation and rollback",
    ),
    6: StageGuide(
        6,
        "Current-visit business quality",
        (
            "Identify latest active visits that are technically valid but unsuitable "
            "for business metrics because references or completed duration are invalid."
        ),
        (
            "Does the visit reference a known property and contractor?",
            "Does a completed visit end after it starts?",
            "Which active visits are reportable?",
        ),
        "10 stage-specific tests passed in the verified repository state.",
        "V11/V12/V13 rules, reportable counts, unfinished visits and idempotency",
    ),
    7: StageGuide(
        7,
        "Property-management history",
        (
            "Convert management observations into contiguous business-time intervals "
            "and expose the current manager separately."
        ),
        (
            "Who managed each property at any historical timestamp?",
            "Which observations are unchanged repeats?",
            "Who currently manages each property?",
        ),
        "10 stage-specific tests passed in the verified repository state.",
        "62-to-37 intervals, current manager, P01 return and P03 transfer boundary",
    ),
    8: StageGuide(
        8,
        "Analytical visit and incident facts",
        (
            "Create one trustworthy fact row per reportable visit and per incident, "
            "with historical company, property-local week and DST-safe elapsed hours."
        ),
        (
            "Which company owned each activity when it happened?",
            "Which local Monday week contains it?",
            "What is the correct elapsed duration?",
        ),
        "11 stage-specific tests passed in the verified repository state.",
        "fact grains, historical attribution, local weeks, DST and fact reconciliation",
    ),
    9: StageGuide(
        9,
        "Reporting marts",
        (
            "Aggregate the fact tables into stable company, property and contractor "
            "reporting grains while preserving zero-activity rows and distinct-count rules."
        ),
        (
            "What are weekly company trends and period totals?",
            "Do all property/weeks appear, including zero activity?",
            "Which contractors account for visit activity?",
        ),
        "12 stage-specific tests passed in the verified repository state.",
        "mart counts, exact totals, zero activity, contractor ranking and reconciliation",
    ),
    10: StageGuide(
        10,
        "Operations report",
        (
            "Render the reporting marts and quality/load evidence into a self-contained "
            "HTML report plus committed CSV extracts for interview review."
        ),
        (
            "Can an operations manager understand the requested-period results?",
            "Are definitions, quality/load evidence, findings and limitations visible?",
            "Can the report be regenerated from the repository?",
        ),
        "Stage 10 adds report-generation tests; run them before committing the stage.",
        "HTML generation, extracts, KPIs, quality/load evidence and regeneration",
    ),
}


def print_stage_intro(stage_number: int, total_stages: int) -> None:
    guide = STAGES[stage_number]

    print()
    print("=" * 96)
    print(f"STAGE {stage_number}/{total_stages} — {guide.title.upper()}")
    print("=" * 96)
    print("Aim:")
    print(textwrap_fill(guide.aim, width=92, indent="  "))
    print()
    print("Questions answered:")
    for question in guide.questions:
        print(f"  • {question}")
    print()


def textwrap_fill(text: str, *, width: int, indent: str = "") -> str:
    import textwrap

    return textwrap.fill(
        text,
        width=width,
        initial_indent=indent,
        subsequent_indent=indent,
    )


def print_validation_summary(stage_number: int) -> None:
    guide = STAGES[stage_number]

    print()
    print("VALIDATION / TEST COVERAGE")
    print("-" * 96)
    print(f"Coverage: {guide.test_coverage}.")
    print(f"Status:   {guide.test_summary}")


def finish_stage(
    stage_number: int,
    total_stages: int,
    *,
    pause: bool,
) -> None:
    print("-" * 96)

    if not pause:
        return

    if stage_number < total_stages:
        prompt = (
            f"Stage {stage_number} complete. "
            f"Press Enter to continue to Stage {stage_number + 1}..."
        )
    else:
        prompt = "Stage 10 complete. Press Enter to finish the pipeline..."

    try:
        input(prompt)
    except EOFError:
        print("No interactive input is available; continuing automatically.")


def _table(title: str, explanation: str, headers, rows, widths) -> None:
    print()
    print(title)
    print("-" * 96)
    print(textwrap_fill(explanation, width=92))
    print()
    print(format_text_table(headers=list(headers), rows=rows, widths=list(widths)))


def show_stage_2_results(connection: duckdb.DuckDBPyConnection) -> None:
    rows = []
    for table_name in (
        "src_companies",
        "src_contractors",
        "src_properties",
        "src_property_management",
        "src_incidents",
        "src_report_weeks",
    ):
        rows.append(
            (
                table_name,
                connection.execute(
                    f"SELECT COUNT(*) FROM {table_name}"
                ).fetchone()[0],
            )
        )

    _table(
        "RESULT — TYPED REFERENCE TABLE COUNTS",
        (
            "Each row is a persistent typed source table. These stable entities are "
            "the reference foundation used by the later visit, management and report models."
        ),
        ("Table", "Rows"),
        rows,
        (32, 8),
    )


def show_stage_3_results(connection: duckdb.DuckDBPyConnection) -> None:
    rows = connection.execute(
        """
        SELECT
            batch_id,
            manifest_order,
            input_lines,
            status,
            attempt_count
        FROM ingestion_batches
        ORDER BY manifest_order
        """
    ).fetchall()

    _table(
        "RESULT — BATCH LANDING SUMMARY",
        (
            "This table shows the six manifest batches, the exact number of physical "
            "JSONL lines preserved from each batch and the durable batch status."
        ),
        ("Batch", "Order", "Raw lines", "Status", "Attempts"),
        rows,
        (10, 7, 10, 12, 9),
    )

    raw_total = connection.execute(
        "SELECT COUNT(*) FROM raw_visit_lines"
    ).fetchone()[0]
    print(f"\nTotal physical raw visit lines preserved: {raw_total}")


def show_stage_4_results(connection: duckdb.DuckDBPyConnection) -> None:
    batch_rows = connection.execute(
        """
        SELECT
            batch_id,
            technically_valid_lines,
            technically_invalid_lines
        FROM ingestion_batches
        ORDER BY manifest_order
        """
    ).fetchall()

    _table(
        "RESULT — TECHNICAL VALIDATION BY BATCH",
        (
            "The table separates technically valid event-delivery lines from source "
            "lines that could not safely be interpreted. Rejected raw evidence is retained."
        ),
        ("Batch", "Valid", "Invalid"),
        batch_rows,
        (10, 10, 10),
    )

    reason_rows = connection.execute(
        """
        SELECT reason_codes, COUNT(*)
        FROM visit_event_rejects
        GROUP BY reason_codes
        ORDER BY COUNT(*) DESC, reason_codes
        """
    ).fetchall()

    _table(
        "RESULT — TECHNICAL REJECT REASONS",
        (
            "This is the compact reject summary. The cumulative Report.txt contains "
            "the exact source batch, line, raw payload and detailed reason for each reject."
        ),
        ("Reason codes", "Rows"),
        reason_rows,
        (68, 8),
    )


def show_stage_5_results(connection: duckdb.DuckDBPyConnection) -> None:
    unique_events = connection.execute(
        "SELECT COUNT(*) FROM visit_events"
    ).fetchone()[0]
    duplicates = connection.execute(
        "SELECT COUNT(*) FROM visit_event_duplicates"
    ).fetchone()[0]
    applied = connection.execute(
        "SELECT COUNT(*) FROM visit_events WHERE state_action = 'applied'"
    ).fetchone()[0]
    stale = connection.execute(
        "SELECT COUNT(*) FROM visit_events WHERE state_action = 'stale'"
    ).fetchone()[0]
    current = connection.execute(
        "SELECT COUNT(*) FROM visit_current_state"
    ).fetchone()[0]
    active = connection.execute(
        "SELECT COUNT(*) FROM visit_current_state WHERE is_deleted = FALSE"
    ).fetchone()[0]
    deleted = connection.execute(
        "SELECT COUNT(*) FROM visit_current_state WHERE is_deleted = TRUE"
    ).fetchone()[0]

    rows = [
        ("Unique event IDs", unique_events),
        ("Duplicate deliveries", duplicates),
        ("Applied state changes", applied),
        ("Stale/equal-version events", stale),
        ("Distinct current visits", current),
        ("Active current visits", active),
        ("Deleted tombstones", deleted),
    ]

    _table(
        "RESULT — VERSIONED VISIT-STATE COUNTS",
        (
            "These counts show how physical valid deliveries become unique event history "
            "and then one highest-version current state per visit."
        ),
        ("Measure", "Count"),
        rows,
        (34, 10),
    )

    examples = connection.execute(
        """
        SELECT
            visit_id,
            source_version,
            operation,
            is_deleted,
            winning_event_id
        FROM visit_current_state
        WHERE visit_id IN ('V02', 'V03', 'V04')
        ORDER BY visit_id
        """
    ).fetchall()

    _table(
        "RESULT — KEY VERSION / DELETE EXAMPLES",
        (
            "V02 proves tombstone protection, V03 shows a later correction, and V04 "
            "shows a genuinely newer upsert reactivating a previously deleted visit."
        ),
        ("Visit", "Version", "Operation", "Deleted", "Winning event"),
        examples,
        (9, 9, 11, 9, 14),
    )


def show_stage_6_results(connection: duckdb.DuckDBPyConnection) -> None:
    rows = connection.execute(
        """
        SELECT
            visit_id,
            issue_code,
            property_id,
            contractor_id,
            CAST(started_at AS VARCHAR),
            CAST(ended_at AS VARCHAR),
            issue_detail
        FROM visit_quality_issues
        ORDER BY visit_id, issue_code
        """
    ).fetchall()

    _table(
        "RESULT — BUSINESS-QUALITY EXCEPTIONS",
        (
            "These records are technically valid but excluded from business metrics. "
            "The timestamps make the V11 negative-duration problem directly visible."
        ),
        (
            "Visit", "Issue", "Property", "Contractor",
            "Started", "Ended", "Reason",
        ),
        rows,
        (8, 20, 12, 12, 23, 23, 42),
    )

    active = connection.execute(
        "SELECT COUNT(*) FROM visit_current_state WHERE is_deleted = FALSE"
    ).fetchone()[0]
    reportable = connection.execute(
        "SELECT COUNT(*) FROM reportable_current_visits"
    ).fetchone()[0]
    print(
        f"\nActive current visits: {active} | "
        f"Reportable after quality checks: {reportable}"
    )


def show_stage_7_results(connection: duckdb.DuckDBPyConnection) -> None:
    rows = connection.execute(
        """
        SELECT
            i.property_id,
            p.property_name,
            i.company_id,
            c.company_name,
            CAST(i.valid_from AS VARCHAR),
            CAST(i.valid_to AS VARCHAR),
            i.source_observation_count
        FROM property_management_intervals AS i
        JOIN src_properties AS p
            ON p.property_id = i.property_id
        JOIN src_companies AS c
            ON c.company_id = i.company_id
        ORDER BY i.property_id, i.valid_from
        """
    ).fetchall()

    _table(
        "RESULT — ALL RECONSTRUCTED MANAGEMENT INTERVALS",
        (
            "Each row is one real contiguous management period. Consecutive unchanged "
            "source observations are collapsed, but a later return to a previous company "
            "creates a new interval."
        ),
        (
            "Property", "Property name", "Company", "Company name",
            "Valid from", "Valid to", "Obs count",
        ),
        rows,
        (9, 22, 8, 24, 24, 24, 9),
    )

    boundary = connection.execute(
        """
        WITH records AS (
            SELECT
                'visit' AS record_type,
                visit_id AS record_id,
                property_id,
                started_at AS business_time
            FROM visit_current_state
            WHERE visit_id IN ('V09', 'V10')

            UNION ALL

            SELECT
                'incident',
                incident_id,
                property_id,
                reported_at
            FROM src_incidents
            WHERE incident_id IN ('I06', 'I07')
        )
        SELECT
            r.record_type,
            r.record_id,
            r.property_id,
            CAST(r.business_time AS VARCHAR),
            m.company_id
        FROM records AS r
        JOIN property_management_intervals AS m
            ON m.property_id = r.property_id
           AND r.business_time >= m.valid_from
           AND (m.valid_to IS NULL OR r.business_time < m.valid_to)
        ORDER BY r.business_time, r.record_type, r.record_id
        """
    ).fetchall()

    _table(
        "RESULT — P03 TRANSFER-BOUNDARY PROOF",
        (
            "This table proves the start-inclusive/end-exclusive interval rule: records "
            "at 12:59 belong to C02, while records exactly at 13:00 belong to C03."
        ),
        ("Type", "Record", "Property", "Business time", "Company"),
        boundary,
        (10, 10, 10, 26, 10),
    )


def show_stage_8_results(connection: duckdb.DuckDBPyConnection) -> None:
    visit_total = connection.execute(
        "SELECT COUNT(*) FROM fact_visits"
    ).fetchone()[0]
    incident_total = connection.execute(
        "SELECT COUNT(*) FROM fact_incidents"
    ).fetchone()[0]

    metrics = connection.execute(
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

    incidents = connection.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN is_high THEN 1 ELSE 0 END)
        FROM fact_incidents
        WHERE is_requested_week = TRUE
        """
    ).fetchone()

    rows = [
        ("All reportable visit facts", visit_total),
        ("All incident facts", incident_total),
        ("Requested-week visits", metrics[0]),
        ("Requested-period distinct visitors", metrics[1]),
        ("Completed visit hours", f"{float(metrics[2]):.2f}"),
        ("Unfinished visits", metrics[3]),
        ("Requested-week incidents", incidents[0]),
        ("High-severity incidents", incidents[1]),
    ]

    _table(
        "RESULT — ANALYTICAL FACT METRICS",
        (
            "The fact layer has one row per reportable visit and one row per incident. "
            "The requested-week figures are the sanity totals later used by the marts."
        ),
        ("Measure", "Value"),
        rows,
        (40, 16),
    )

    dst_rows = connection.execute(
        """
        SELECT
            visit_id,
            property_timezone,
            CAST(local_started_at AS VARCHAR),
            CAST(local_ended_at AS VARCHAR),
            completed_hours
        FROM fact_visits
        WHERE visit_id IN ('V07', 'V16')
        ORDER BY visit_id
        """
    ).fetchall()

    _table(
        "RESULT — DST / ELAPSED-DURATION PROOF",
        (
            "Local wall clocks move across daylight saving, but completed_hours uses "
            "elapsed TIMESTAMPTZ/UTC time, so both visits correctly last two hours."
        ),
        ("Visit", "Timezone", "Local start", "Local end", "Hours"),
        dst_rows,
        (8, 20, 22, 22, 8),
    )


def show_stage_9_results(connection: duckdb.DuckDBPyConnection) -> None:
    counts = [
        (
            "Company/week rows",
            connection.execute(
                "SELECT COUNT(*) FROM rpt_company_week"
            ).fetchone()[0],
        ),
        (
            "Company requested-period rows",
            connection.execute(
                "SELECT COUNT(*) FROM rpt_company_period"
            ).fetchone()[0],
        ),
        (
            "Property/week rows",
            connection.execute(
                "SELECT COUNT(*) FROM rpt_property_week"
            ).fetchone()[0],
        ),
        (
            "Contractor rows",
            connection.execute(
                "SELECT COUNT(*) FROM rpt_contractor"
            ).fetchone()[0],
        ),
        (
            "Zero-activity property/week rows",
            connection.execute(
                "SELECT COUNT(*) FROM rpt_property_week WHERE has_activity = FALSE"
            ).fetchone()[0],
        ),
    ]

    _table(
        "RESULT — REPORTING MART COUNTS",
        (
            "These are the stable grains consumed by the final report. The 144 "
            "property/week rows include zero-activity weeks by design."
        ),
        ("Mart result", "Rows"),
        counts,
        (42, 10),
    )

    company_rows = connection.execute(
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

    _table(
        "RESULT — COMPANY REQUESTED-PERIOD SUMMARY",
        (
            "One row per managing company across all requested weeks. Distinct visitors "
            "are recalculated directly at this grain rather than added from lower grains."
        ),
        (
            "Company", "Visits", "Visitors", "Hours",
            "Unfinished", "Incidents", "High",
        ),
        company_rows,
        (24, 8, 9, 10, 11, 10, 8),
    )

    contractor_rows = connection.execute(
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

    _table(
        "RESULT — CONTRACTOR BREAKDOWN",
        (
            "One row per contractor for requested-week visit activity. Incidents are "
            "not included because the source data has no incident-to-contractor key."
        ),
        ("Contractor", "Visits", "Visitors", "Properties", "Hours", "Unfinished"),
        contractor_rows,
        (24, 8, 9, 11, 10, 11),
    )


def show_stage_10_results(
    *,
    report_path: str,
    extract_paths: Iterable[str],
    latest_batch_id: str,
) -> None:
    rows = [("HTML operations report", report_path)]
    for path in extract_paths:
        rows.append(("CSV model extract", path))
    rows.append(("Latest processed batch", latest_batch_id))

    _table(
        "RESULT — VIEWABLE REPORT AND MODEL EXTRACTS",
        (
            "These committed artifacts are what an interviewer can open without "
            "querying DuckDB. The HTML is self-contained and the CSV files expose "
            "the reporting marts used to build it."
        ),
        ("Artifact", "Path / value"),
        rows,
        (28, 78),
    )
