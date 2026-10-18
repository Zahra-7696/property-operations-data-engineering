from __future__ import annotations

from pathlib import Path

import pytest

from src.audit_report import write_processing_report
from src.build_analytical_facts import build_analytical_facts
from src.build_management_history import build_management_history
from src.build_reporting_marts import build_reporting_marts
from src.check_visit_quality import check_visit_quality
from src.database import connect_database
from src.land_visit_batches import land_visit_batches
from src.load_reference_data import load_reference_data
from src.process_visit_state import process_visit_state
from src.validate_visit_events import validate_visit_events


@pytest.fixture(scope="module")
def marts_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("stage9") / "stage9.duckdb"

    load_reference_data(db_path=db_path)
    land_visit_batches(db_path=db_path)
    validate_visit_events(db_path=db_path)
    process_visit_state(db_path=db_path)
    check_visit_quality(db_path=db_path)
    build_management_history(db_path=db_path)
    build_analytical_facts(db_path=db_path)
    build_reporting_marts(db_path=db_path)

    return db_path


def test_expected_mart_row_counts(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        actual = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM rpt_company_week),
                (SELECT COUNT(*) FROM rpt_company_period),
                (SELECT COUNT(*) FROM rpt_property_week),
                (SELECT COUNT(*) FROM rpt_contractor)
            """
        ).fetchone()
    finally:
        connection.close()

    assert actual == (18, 3, 144, 8)


def test_company_period_expected_metrics(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        actual = connection.execute(
            """
            SELECT
                company_id,
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
    finally:
        connection.close()

    assert actual == [
        ("C01", 514, 228, 639.75, 32, 89, 8),
        ("C02", 511, 222, 684.75, 29, 83, 14),
        ("C03", 518, 223, 729.0, 30, 74, 9),
    ]


def test_company_week_rows_reconcile_to_facts(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        mismatches = connection.execute(
            """
            WITH expected_visits AS (
                SELECT
                    company_id,
                    week_start_local AS week_start,
                    COUNT(*) AS visit_count,
                    COUNT(DISTINCT visitor_id) AS distinct_visitors,
                    ROUND(COALESCE(SUM(completed_hours), 0), 2) AS hours,
                    SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END) AS unfinished
                FROM fact_visits
                WHERE is_requested_week = TRUE
                GROUP BY company_id, week_start_local
            ),
            expected_incidents AS (
                SELECT
                    company_id,
                    week_start_local AS week_start,
                    COUNT(*) AS incidents,
                    SUM(CASE WHEN is_high THEN 1 ELSE 0 END) AS high_incidents
                FROM fact_incidents
                WHERE is_requested_week = TRUE
                GROUP BY company_id, week_start_local
            )
            SELECT COUNT(*)
            FROM rpt_company_week AS r
            LEFT JOIN expected_visits AS v
                ON v.company_id = r.company_id
               AND v.week_start = r.week_start
            LEFT JOIN expected_incidents AS i
                ON i.company_id = r.company_id
               AND i.week_start = r.week_start
            WHERE r.visit_count <> COALESCE(v.visit_count, 0)
               OR r.distinct_visitors <> COALESCE(v.distinct_visitors, 0)
               OR r.completed_visit_hours <> COALESCE(v.hours, 0)
               OR r.unfinished_visits <> COALESCE(v.unfinished, 0)
               OR r.incident_count <> COALESCE(i.incidents, 0)
               OR r.high_severity_incidents <> COALESCE(i.high_incidents, 0)
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert mismatches == 0


def test_property_week_scaffold_includes_zero_activity(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        total = connection.execute(
            "SELECT COUNT(*) FROM rpt_property_week"
        ).fetchone()[0]

        zero_rows = connection.execute(
            """
            SELECT COUNT(*)
            FROM rpt_property_week
            WHERE has_activity = FALSE
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert total == 24 * 6 == 144
    assert zero_rows == 22


def test_zero_activity_rows_really_have_zero_metrics(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        invalid = connection.execute(
            """
            SELECT COUNT(*)
            FROM rpt_property_week
            WHERE has_activity = FALSE
              AND (
                    visit_count <> 0
                    OR distinct_visitors <> 0
                    OR completed_visit_hours <> 0
                    OR unfinished_visits <> 0
                    OR incident_count <> 0
                    OR high_severity_incidents <> 0
              )
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert invalid == 0


def test_property_week_totals_reconcile_to_requested_facts(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        totals = connection.execute(
            """
            SELECT
                SUM(visit_count),
                ROUND(SUM(completed_visit_hours), 2),
                SUM(unfinished_visits),
                SUM(incident_count),
                SUM(high_severity_incidents)
            FROM rpt_property_week
            """
        ).fetchone()
    finally:
        connection.close()

    assert totals == (1543, 2053.5, 91, 246, 31)


def test_contractor_breakdown_reconciles_to_visits(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        row_count, visits, hours, unfinished = connection.execute(
            """
            SELECT
                COUNT(*),
                SUM(visit_count),
                ROUND(SUM(completed_visit_hours), 2),
                SUM(unfinished_visits)
            FROM rpt_contractor
            """
        ).fetchone()
    finally:
        connection.close()

    assert row_count == 8
    assert visits == 1543
    assert hours == 2053.5
    assert unfinished == 91


def test_top_three_contractors_by_visit_count(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        actual = connection.execute(
            """
            SELECT contractor_name, visit_count
            FROM rpt_contractor
            ORDER BY visit_count DESC, contractor_id
            LIMIT 3
            """
        ).fetchall()
    finally:
        connection.close()

    assert actual == [
        ("Field Crew 07", 217),
        ("Field Crew 02", 208),
        ("Field Crew 06", 198),
    ]


def test_distinct_visitors_are_not_additive_across_companies(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        overall = connection.execute(
            """
            SELECT COUNT(DISTINCT visitor_id)
            FROM fact_visits
            WHERE is_requested_week = TRUE
            """
        ).fetchone()[0]

        company_sum = connection.execute(
            "SELECT SUM(distinct_visitors) FROM rpt_company_period"
        ).fetchone()[0]
    finally:
        connection.close()

    assert overall == 259
    assert company_sum == 673
    assert company_sum > overall


def test_company_period_partition_totals_match_requested_facts(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        totals = connection.execute(
            """
            SELECT
                SUM(visit_count),
                ROUND(SUM(completed_visit_hours), 2),
                SUM(unfinished_visits),
                SUM(incident_count),
                SUM(high_severity_incidents)
            FROM rpt_company_period
            """
        ).fetchone()
    finally:
        connection.close()

    assert totals == (1543, 2053.5, 91, 246, 31)


def test_reporting_mart_rebuild_is_idempotent(marts_db):
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        before_company = connection.execute(
            """
            SELECT * FROM rpt_company_week
            ORDER BY week_start, company_id
            """
        ).fetchall()

        before_property = connection.execute(
            """
            SELECT * FROM rpt_property_week
            ORDER BY property_id, week_start
            """
        ).fetchall()

        before_contractor = connection.execute(
            """
            SELECT * FROM rpt_contractor
            ORDER BY contractor_id
            """
        ).fetchall()
    finally:
        connection.close()

    result = build_reporting_marts(db_path=marts_db)

    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        after_company = connection.execute(
            """
            SELECT * FROM rpt_company_week
            ORDER BY week_start, company_id
            """
        ).fetchall()

        after_property = connection.execute(
            """
            SELECT * FROM rpt_property_week
            ORDER BY property_id, week_start
            """
        ).fetchall()

        after_contractor = connection.execute(
            """
            SELECT * FROM rpt_contractor
            ORDER BY contractor_id
            """
        ).fetchall()
    finally:
        connection.close()

    assert result.company_week_rows == 18
    assert result.property_week_rows == 144
    assert result.zero_activity_property_weeks == 22
    assert before_company == after_company
    assert before_property == after_property
    assert before_contractor == after_contractor


def test_cumulative_report_contains_stage9_evidence(marts_db, tmp_path):
    report_path = tmp_path / "Report.txt"
    connection = connect_database(read_only=True, db_path=marts_db)
    try:
        write_processing_report(connection, report_path=report_path)
    finally:
        connection.close()

    report = report_path.read_text(encoding="utf-8")

    assert "STAGE 9 - REPORTING MARTS" in report
    assert "Company/week rows               : 18" in report
    assert "Property/week rows              : 144" in report
    assert "Zero-activity property/weeks    : 22" in report

    assert "Clover Management" in report
    assert "Fern Management" in report
    assert "Wattle Management" in report

    assert "Field Crew 07" in report
    assert "Field Crew 02" in report
    assert "Field Crew 06" in report

    assert "Overall requested-period distinct visitors : 259" in report
    assert "Sum of company distinct visitors           : 673" in report
