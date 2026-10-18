from __future__ import annotations

from pathlib import Path

import pytest

from src.audit_report import write_processing_report
from src.check_visit_quality import check_visit_quality
from src.database import connect_database
from src.land_visit_batches import land_visit_batches
from src.load_reference_data import load_reference_data
from src.process_visit_state import process_visit_state
from src.validate_visit_events import validate_visit_events


@pytest.fixture(scope="module")
def quality_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("stage6") / "stage6.duckdb"
    load_reference_data(db_path=db_path)
    land_visit_batches(db_path=db_path)
    validate_visit_events(db_path=db_path)
    process_visit_state(db_path=db_path)
    check_visit_quality(db_path=db_path)
    return db_path


def test_expected_business_quality_totals(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        issue_rows = connection.execute(
            "SELECT COUNT(*) FROM visit_quality_issues"
        ).fetchone()[0]

        affected_visits = connection.execute(
            "SELECT COUNT(DISTINCT visit_id) FROM visit_quality_issues"
        ).fetchone()[0]

        reportable = connection.execute(
            "SELECT COUNT(*) FROM reportable_current_visits"
        ).fetchone()[0]
    finally:
        connection.close()

    assert issue_rows == 3
    assert affected_visits == 3
    assert reportable == 1564


def test_expected_three_quality_issues(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        actual = connection.execute(
            """
            SELECT visit_id, issue_code, winning_event_id
            FROM visit_quality_issues
            ORDER BY visit_id
            """
        ).fetchall()
    finally:
        connection.close()

    assert actual == [
        ("V11", "end_before_start", "E00011"),
        ("V12", "unknown_property", "E00012"),
        ("V13", "unknown_contractor", "E00013"),
    ]


def test_v11_end_before_start_is_excluded(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        issue = connection.execute(
            """
            SELECT
                issue_code,
                started_at = TIMESTAMPTZ '2026-03-25 01:00:00+00:00',
                ended_at = TIMESTAMPTZ '2026-03-25 00:00:00+00:00',
                EXTRACT(EPOCH FROM (ended_at - started_at)) / 60.0
            FROM visit_quality_issues
            WHERE visit_id = 'V11'
            """
        ).fetchone()

        reportable = connection.execute(
            """
            SELECT COUNT(*)
            FROM reportable_current_visits
            WHERE visit_id = 'V11'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert issue == ("end_before_start", True, True, -60.0)
    assert reportable == 0


def test_v12_unknown_property_is_excluded(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        issue = connection.execute(
            """
            SELECT issue_code, property_id
            FROM visit_quality_issues
            WHERE visit_id = 'V12'
            """
        ).fetchone()

        reportable = connection.execute(
            """
            SELECT COUNT(*)
            FROM reportable_current_visits
            WHERE visit_id = 'V12'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert issue == ("unknown_property", "P_UNKNOWN")
    assert reportable == 0


def test_v13_unknown_contractor_is_excluded(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        issue = connection.execute(
            """
            SELECT issue_code, contractor_id
            FROM visit_quality_issues
            WHERE visit_id = 'V13'
            """
        ).fetchone()

        reportable = connection.execute(
            """
            SELECT COUNT(*)
            FROM reportable_current_visits
            WHERE visit_id = 'V13'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert issue == ("unknown_contractor", "K_UNKNOWN")
    assert reportable == 0


def test_deleted_visits_are_not_quality_failures(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        deleted_issues = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_quality_issues AS q
            JOIN visit_current_state AS s
                ON s.visit_id = q.visit_id
            WHERE s.is_deleted = TRUE
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert deleted_issues == 0


def test_unfinished_visits_are_valid_and_reportable(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        unfinished_reportable = connection.execute(
            """
            SELECT COUNT(*)
            FROM reportable_current_visits
            WHERE ended_at IS NULL
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert unfinished_reportable > 0


def test_quality_check_is_idempotent(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)
    try:
        before = connection.execute(
            """
            SELECT
                visit_id,
                issue_code,
                winning_event_id,
                source_batch_id,
                source_line_number
            FROM visit_quality_issues
            ORDER BY visit_id, issue_code
            """
        ).fetchall()
    finally:
        connection.close()

    result = check_visit_quality(db_path=quality_db)

    connection = connect_database(read_only=True, db_path=quality_db)
    try:
        after = connection.execute(
            """
            SELECT
                visit_id,
                issue_code,
                winning_event_id,
                source_batch_id,
                source_line_number
            FROM visit_quality_issues
            ORDER BY visit_id, issue_code
            """
        ).fetchall()
    finally:
        connection.close()

    assert result.issue_rows == 3
    assert result.reportable_visits == 1564
    assert before == after


def test_cumulative_report_contains_stage6_evidence(quality_db, tmp_path):
    report_path = tmp_path / "Report.txt"
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        write_processing_report(connection, report_path=report_path)
    finally:
        connection.close()

    report = report_path.read_text(encoding="utf-8")

    assert "STAGE 6 - CURRENT VISIT BUSINESS QUALITY" in report
    assert "\n  Business-quality issue rows        : 3\n" in report
    assert "\n  Active visits affected             : 3\n" in report
    assert "\n  Reportable active visits           : 1564\n" in report

    assert "V11" in report
    assert "end_before_start" in report
    assert "2026-03-25T01:00:00Z" in report
    assert "2026-03-25T00:00:00Z" in report
    assert "-60.0" in report
    assert "V12" in report
    assert "unknown_property" in report
    assert "V13" in report
    assert "unknown_contractor" in report

    assert "data/visit_changes/batch_01.jsonl:242" in report
    assert "data/visit_changes/batch_01.jsonl:270" in report
    assert "data/visit_changes/batch_01.jsonl:296" in report



def test_business_quality_batch_counter(quality_db):
    connection = connect_database(read_only=True, db_path=quality_db)

    try:
        actual = connection.execute(
            """
            SELECT batch_id, business_quality_issues
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()
    finally:
        connection.close()

    assert actual == [
        ("batch_01", 3),
        ("batch_02", 0),
        ("batch_03", 0),
        ("batch_04", 0),
        ("batch_05", 0),
        ("batch_06", 0),
    ]
