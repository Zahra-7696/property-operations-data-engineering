from __future__ import annotations

from pathlib import Path

import pytest

from src.audit_report import write_processing_report
from src.build_analytical_facts import build_analytical_facts
from src.build_management_history import build_management_history
from src.check_visit_quality import check_visit_quality
from src.database import connect_database
from src.land_visit_batches import land_visit_batches
from src.load_reference_data import load_reference_data
from src.process_visit_state import process_visit_state
from src.validate_visit_events import validate_visit_events


@pytest.fixture(scope="module")
def facts_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("stage8") / "stage8.duckdb"

    load_reference_data(db_path=db_path)
    land_visit_batches(db_path=db_path)
    validate_visit_events(db_path=db_path)
    process_visit_state(db_path=db_path)
    check_visit_quality(db_path=db_path)
    build_management_history(db_path=db_path)
    build_analytical_facts(db_path=db_path)

    return db_path


def test_expected_fact_counts(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        visit_count = connection.execute(
            "SELECT COUNT(*) FROM fact_visits"
        ).fetchone()[0]

        incident_count = connection.execute(
            "SELECT COUNT(*) FROM fact_incidents"
        ).fetchone()[0]
    finally:
        connection.close()

    assert visit_count == 1564
    assert incident_count == 247


def test_requested_week_counts_and_outside_records(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        requested_visits = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_visits
            WHERE is_requested_week = TRUE
            """
        ).fetchone()[0]

        outside_visits = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_visits
            WHERE is_requested_week = FALSE
            """
        ).fetchone()[0]

        requested_incidents = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_incidents
            WHERE is_requested_week = TRUE
            """
        ).fetchone()[0]

        outside_incidents = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_incidents
            WHERE is_requested_week = FALSE
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert requested_visits == 1543
    assert outside_visits == 21
    assert requested_incidents == 246
    assert outside_incidents == 1


def test_fact_grains_remain_unique(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        visit_total, visit_distinct = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT visit_id)
            FROM fact_visits
            """
        ).fetchone()

        incident_total, incident_distinct = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT incident_id)
            FROM fact_incidents
            """
        ).fetchone()
    finally:
        connection.close()

    assert visit_total == visit_distinct == 1564
    assert incident_total == incident_distinct == 247


def test_every_fact_has_historical_company_and_local_week(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        invalid_visits = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_visits
            WHERE company_id IS NULL
               OR week_start_local IS NULL
               OR local_started_at IS NULL
            """
        ).fetchone()[0]

        invalid_incidents = connection.execute(
            """
            SELECT COUNT(*)
            FROM fact_incidents
            WHERE company_id IS NULL
               OR week_start_local IS NULL
               OR local_reported_at IS NULL
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert invalid_visits == 0
    assert invalid_incidents == 0


def test_historical_company_attribution_uses_business_time(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        visits = connection.execute(
            """
            SELECT visit_id, company_id
            FROM fact_visits
            WHERE visit_id IN ('V09', 'V10')
            ORDER BY visit_id
            """
        ).fetchall()

        incidents = connection.execute(
            """
            SELECT incident_id, company_id
            FROM fact_incidents
            WHERE incident_id IN ('I06', 'I07')
            ORDER BY incident_id
            """
        ).fetchall()
    finally:
        connection.close()

    assert visits == [("V09", "C02"), ("V10", "C03")]
    assert incidents == [("I06", "C03"), ("I07", "C02")]


def test_property_local_week_boundary(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        actual = connection.execute(
            """
            SELECT
                visit_id,
                CAST(local_started_at AS VARCHAR),
                CAST(week_start_local AS VARCHAR)
            FROM fact_visits
            WHERE visit_id IN ('V14', 'V01')
            ORDER BY visit_id
            """
        ).fetchall()
    finally:
        connection.close()

    assert actual == [
        ("V01", "2026-03-23 00:00:00", "2026-03-23"),
        ("V14", "2026-03-22 23:59:00", "2026-03-16"),
    ]


def test_dst_crossing_visits_use_elapsed_utc_hours(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        actual = connection.execute(
            """
            SELECT
                visit_id,
                CAST(local_started_at AS VARCHAR),
                CAST(local_ended_at AS VARCHAR),
                completed_hours,
                CAST(week_start_local AS VARCHAR)
            FROM fact_visits
            WHERE visit_id IN ('V07', 'V16')
            ORDER BY visit_id
            """
        ).fetchall()
    finally:
        connection.close()

    assert actual == [
        ("V07", "2026-04-05 02:30:00", "2026-04-05 03:30:00", 2.0, "2026-03-30"),
        ("V16", "2026-04-05 02:30:00", "2026-04-05 03:30:00", 2.0, "2026-03-30"),
    ]


def test_requested_week_metric_sanity_checks(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        visit_metrics = connection.execute(
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
            SELECT
                COUNT(*),
                SUM(CASE WHEN is_high THEN 1 ELSE 0 END)
            FROM fact_incidents
            WHERE is_requested_week = TRUE
            """
        ).fetchone()
    finally:
        connection.close()

    assert visit_metrics == (1543, 259, 2053.5, 91)
    assert incident_metrics == (246, 31)


def test_unfinished_visits_contribute_no_completed_hours(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        all_unfinished, invalid_hours = connection.execute(
            """
            SELECT
                COUNT(*),
                SUM(
                    CASE
                        WHEN completed_hours IS NOT NULL THEN 1
                        ELSE 0
                    END
                )
            FROM fact_visits
            WHERE is_unfinished = TRUE
            """
        ).fetchone()
    finally:
        connection.close()

    assert all_unfinished == 92
    assert invalid_hours == 0


def test_analytical_fact_rebuild_is_idempotent(facts_db):
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        before_visits = connection.execute(
            """
            SELECT *
            FROM fact_visits
            ORDER BY visit_id
            """
        ).fetchall()

        before_incidents = connection.execute(
            """
            SELECT *
            FROM fact_incidents
            ORDER BY incident_id
            """
        ).fetchall()
    finally:
        connection.close()

    result = build_analytical_facts(db_path=facts_db)

    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        after_visits = connection.execute(
            """
            SELECT *
            FROM fact_visits
            ORDER BY visit_id
            """
        ).fetchall()

        after_incidents = connection.execute(
            """
            SELECT *
            FROM fact_incidents
            ORDER BY incident_id
            """
        ).fetchall()
    finally:
        connection.close()

    assert result.visit_facts == 1564
    assert result.incident_facts == 247
    assert before_visits == after_visits
    assert before_incidents == after_incidents


def test_cumulative_report_contains_stage8_evidence(facts_db, tmp_path):
    report_path = tmp_path / "Report.txt"
    connection = connect_database(read_only=True, db_path=facts_db)

    try:
        write_processing_report(connection, report_path=report_path)
    finally:
        connection.close()

    report = report_path.read_text(encoding="utf-8")

    assert "STAGE 8 - ANALYTICAL VISIT AND INCIDENT FACTS" in report
    assert "Visit facts (all reportable current visits) : 1564" in report
    assert "Incident facts (all supplied incidents)     : 247" in report
    assert "Visits in requested local weeks             : 1543" in report
    assert "Incidents in requested local weeks          : 246" in report

    assert "Distinct visitors       : 259" in report
    assert "Completed visit hours   : 2053.50" in report
    assert "Unfinished visits       : 91" in report
    assert "High-severity incidents : 31" in report

    assert "V14" in report
    assert "V01" in report
    assert "V07" in report
    assert "V16" in report
    assert "V09" in report
    assert "V10" in report
    assert "I06" in report
    assert "I07" in report
