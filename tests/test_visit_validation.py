from __future__ import annotations

from pathlib import Path

import pytest

from src.audit_report import write_processing_report
from src.database import connect_database
from src.land_visit_batches import land_visit_batches
from src.validate_visit_events import (
    validate_event_payload,
    validate_visit_events,
)


EXPECTED_BATCH_COUNTS = [
    ("batch_01", 378, 3),
    ("batch_02", 320, 3),
    ("batch_03", 320, 3),
    ("batch_04", 320, 3),
    ("batch_05", 320, 3),
    ("batch_06", 321, 3),
]


@pytest.fixture(scope="module")
def validated_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("stage4") / "stage4.duckdb"
    land_visit_batches(db_path=db_path)
    validate_visit_events(db_path=db_path)
    return db_path


def test_expected_technical_validation_totals(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        valid_count = connection.execute(
            "SELECT COUNT(*) FROM validated_visit_events"
        ).fetchone()[0]
        reject_count = connection.execute(
            "SELECT COUNT(*) FROM visit_event_rejects"
        ).fetchone()[0]
    finally:
        connection.close()

    assert valid_count == 1979
    assert reject_count == 18


def test_each_batch_has_expected_valid_and_invalid_counts(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        actual = connection.execute(
            """
            SELECT
                batch_id,
                technically_valid_lines,
                technically_invalid_lines
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()
    finally:
        connection.close()

    assert actual == EXPECTED_BATCH_COUNTS


def test_all_invalid_source_lines_remain_auditable(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        incomplete = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_event_rejects
            WHERE
                batch_id IS NULL
                OR line_number IS NULL
                OR raw_payload IS NULL
                OR reason_codes = ''
                OR rejection_reason = ''
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert incomplete == 0


def test_six_lines_are_rejected_as_malformed_json(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_event_rejects
            WHERE reason_codes = 'malformed_json'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert count == 6


def test_six_bad_timestamp_delete_events_are_rejected(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_event_rejects
            WHERE reason_codes = 'invalid_updated_at'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert count == 6


def test_six_incomplete_upserts_are_rejected(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_event_rejects
            WHERE reason_codes LIKE '%invalid_visit_id%'
              AND reason_codes LIKE '%invalid_source_version%'
              AND reason_codes LIKE '%missing_ended_at%'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert count == 6


def test_validated_upserts_have_complete_technical_payload(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        invalid = connection.execute(
            """
            SELECT COUNT(*)
            FROM validated_visit_events
            WHERE operation = 'upsert'
              AND (
                    property_id IS NULL
                    OR contractor_id IS NULL
                    OR visitor_id IS NULL
                    OR started_at IS NULL
              )
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert invalid == 0


def test_validated_deletes_omit_visit_payload(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        invalid = connection.execute(
            """
            SELECT COUNT(*)
            FROM validated_visit_events
            WHERE operation = 'delete'
              AND (
                    property_id IS NOT NULL
                    OR contractor_id IS NOT NULL
                    OR visitor_id IS NOT NULL
                    OR started_at IS NOT NULL
                    OR ended_at IS NOT NULL
              )
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert invalid == 0


def test_validation_replay_is_a_true_no_op(validated_db):
    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        before_batches = connection.execute(
            """
            SELECT
                batch_id,
                validated_at,
                technically_valid_lines,
                technically_invalid_lines,
                attempt_count
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()
        before_valid = connection.execute(
            "SELECT COUNT(*) FROM validated_visit_events"
        ).fetchone()[0]
        before_rejects = connection.execute(
            "SELECT COUNT(*) FROM visit_event_rejects"
        ).fetchone()[0]
    finally:
        connection.close()

    results = validate_visit_events(db_path=validated_db)

    connection = connect_database(read_only=True, db_path=validated_db)
    try:
        after_batches = connection.execute(
            """
            SELECT
                batch_id,
                validated_at,
                technically_valid_lines,
                technically_invalid_lines,
                attempt_count
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()
        after_valid = connection.execute(
            "SELECT COUNT(*) FROM validated_visit_events"
        ).fetchone()[0]
        after_rejects = connection.execute(
            "SELECT COUNT(*) FROM visit_event_rejects"
        ).fetchone()[0]
    finally:
        connection.close()

    assert all(result.action == "skipped" for result in results)
    assert before_batches == after_batches
    assert before_valid == after_valid == 1979
    assert before_rejects == after_rejects == 18


def test_boolean_source_version_is_invalid():
    raw = (
        '{"event_id":"E1","visit_id":"V1","source_version":true,'
        '"operation":"delete","updated_at":"2026-04-01T00:00:00+00:00"}'
    )
    result = validate_event_payload(raw)
    assert result.is_valid is False
    assert "invalid_source_version" in result.reason_codes


def test_naive_timestamp_is_invalid():
    raw = (
        '{"event_id":"E1","visit_id":"V1","source_version":1,'
        '"operation":"delete","updated_at":"2026-04-01T00:00:00"}'
    )
    result = validate_event_payload(raw)
    assert result.is_valid is False
    assert "invalid_updated_at" in result.reason_codes

def test_cumulative_report_contains_stage4_reject_evidence(validated_db, tmp_path):
    report_path = tmp_path / "Report.txt"
    connection = connect_database(read_only=True, db_path=validated_db)

    try:
        write_processing_report(
            connection,
            report_path=report_path,
        )
    finally:
        connection.close()

    report = report_path.read_text(encoding="utf-8")

    assert "STAGE 1 - SOURCE INTEGRITY" in report
    assert "STAGE 2 - TYPED REFERENCE LAYER" in report
    assert "STAGE 3 - RAW VISIT BATCH LANDING" in report
    assert "STAGE 4 - TECHNICAL VISIT-EVENT VALIDATION" in report

    assert "Technically valid event-delivery lines   : 1979" in report
    assert "Technically invalid event-delivery lines : 18" in report

    # Human-facing reject categories are intentionally formatted for readability.
    assert "Malformed JSON" in report
    assert "Incomplete upsert" in report
    assert "Invalid timestamp" in report

    # Detailed evidence still preserves the underlying machine-readable reason codes.
    assert "malformed_json" in report
    assert "invalid_visit_id" in report
    assert "invalid_updated_at" in report
    assert "| #  | Batch    | Line  | Event ID     | Category           | Reason" in report
    assert "| #  | Source reference" in report

    assert "data/visit_changes/batch_01.jsonl:379" in report
    assert "data/visit_changes/batch_06.jsonl:324" in report
    assert '{"event_id":"TRUNCATED",' in report

