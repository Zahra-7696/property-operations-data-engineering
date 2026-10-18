from __future__ import annotations

from pathlib import Path

import pytest

from src.audit_report import write_processing_report
from src.database import connect_database
from src.land_visit_batches import land_visit_batches
from src.process_visit_state import process_visit_state
from src.validate_visit_events import validate_visit_events


EXPECTED_BATCH_COUNTS = [
    ("batch_01", 368, 10, 368, 0),
    ("batch_02", 315, 5, 295, 20),
    ("batch_03", 315, 5, 307, 8),
    ("batch_04", 315, 5, 305, 10),
    ("batch_05", 315, 5, 308, 7),
    ("batch_06", 316, 5, 307, 9),
]


@pytest.fixture(scope="module")
def state_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("stage5") / "stage5.duckdb"
    land_visit_batches(db_path=db_path)
    validate_visit_events(db_path=db_path)
    process_visit_state(db_path=db_path)
    return db_path


def test_expected_batch_processing_counts(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        actual = connection.execute(
            """
            SELECT
                batch_id,
                unique_event_lines,
                duplicate_event_lines,
                applied_state_changes,
                stale_event_lines
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()
    finally:
        connection.close()

    assert actual == EXPECTED_BATCH_COUNTS


def test_expected_event_and_visit_totals(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        unique_events = connection.execute(
            "SELECT COUNT(*) FROM visit_events"
        ).fetchone()[0]
        duplicates = connection.execute(
            "SELECT COUNT(*) FROM visit_event_duplicates"
        ).fetchone()[0]
        states = connection.execute(
            "SELECT COUNT(*) FROM visit_current_state"
        ).fetchone()[0]
    finally:
        connection.close()

    assert unique_events == 1944
    assert duplicates == 35
    assert states == 1618


def test_expected_active_and_deleted_visit_counts(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        active = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_current_state
            WHERE is_deleted = FALSE
            """
        ).fetchone()[0]
        deleted = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_current_state
            WHERE is_deleted = TRUE
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert active == 1567
    assert deleted == 51


def test_unique_event_history_is_really_unique(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        total, distinct_total = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT event_id)
            FROM visit_events
            """
        ).fetchone()
    finally:
        connection.close()

    assert total == distinct_total == 1944


def test_all_duplicate_deliveries_link_to_a_canonical_event(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        missing = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_event_duplicates AS d
            LEFT JOIN visit_events AS e
                ON e.event_id = d.event_id
               AND e.first_batch_id = d.canonical_batch_id
               AND e.first_line_number = d.canonical_line_number
            WHERE e.event_id IS NULL
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert missing == 0


def test_v02_delete_tombstone_blocks_older_upsert(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        current = connection.execute(
            """
            SELECT
                source_version,
                operation,
                is_deleted,
                winning_event_id
            FROM visit_current_state
            WHERE visit_id = 'V02'
            """
        ).fetchone()

        old_repeat = connection.execute(
            """
            SELECT state_action, source_version
            FROM visit_events
            WHERE event_id = 'E01943'
            """
        ).fetchone()
    finally:
        connection.close()

    assert current == (2, "delete", True, "E00683")
    assert old_repeat == ("stale", 1)


def test_v04_newer_upsert_reactivates_deleted_visit(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        current = connection.execute(
            """
            SELECT
                source_version,
                operation,
                is_deleted,
                winning_event_id
            FROM visit_current_state
            WHERE visit_id = 'V04'
            """
        ).fetchone()
    finally:
        connection.close()

    assert current == (3, "upsert", False, "E01628")


def test_v03_latest_correction_is_current(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        current = connection.execute(
            """
            SELECT
                source_version,
                operation,
                winning_event_id,
                ended_at = TIMESTAMPTZ '2026-03-27 22:00:00+00:00'
                    AS ended_at_matches_expected_instant
            FROM visit_current_state
            WHERE visit_id = 'V03'
            """
        ).fetchone()
    finally:
        connection.close()

    assert current == (2, "upsert", "E00998", True)


def test_completed_batch_replay_is_a_true_no_op(state_db):
    connection = connect_database(read_only=True, db_path=state_db)
    try:
        before_batches = connection.execute(
            """
            SELECT
                batch_id,
                status,
                completed_at,
                unique_event_lines,
                duplicate_event_lines,
                applied_state_changes,
                stale_event_lines
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()

        before_counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM visit_events),
                (SELECT COUNT(*) FROM visit_event_duplicates),
                (SELECT COUNT(*) FROM visit_current_state)
            """
        ).fetchone()
    finally:
        connection.close()

    results = process_visit_state(db_path=state_db)

    connection = connect_database(read_only=True, db_path=state_db)
    try:
        after_batches = connection.execute(
            """
            SELECT
                batch_id,
                status,
                completed_at,
                unique_event_lines,
                duplicate_event_lines,
                applied_state_changes,
                stale_event_lines
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()

        after_counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM visit_events),
                (SELECT COUNT(*) FROM visit_event_duplicates),
                (SELECT COUNT(*) FROM visit_current_state)
            """
        ).fetchone()
    finally:
        connection.close()

    assert all(result.action == "skipped" for result in results)
    assert before_batches == after_batches
    assert before_counts == after_counts == (1944, 35, 1618)


def test_failed_state_batch_rolls_back_and_can_restart(tmp_path):
    db_path = tmp_path / "stage5_restart.duckdb"

    land_visit_batches(db_path=db_path)
    validate_visit_events(db_path=db_path)

    with pytest.raises(RuntimeError, match="Simulated state-processing failure"):
        process_visit_state(
            db_path=db_path,
            fail_batch_id="batch_03",
            fail_after_unique_events=10,
        )

    connection = connect_database(read_only=True, db_path=db_path)
    try:
        statuses = connection.execute(
            """
            SELECT batch_id, status
            FROM ingestion_batches
            WHERE manifest_order <= 3
            ORDER BY manifest_order
            """
        ).fetchall()

        batch_03_events = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_events
            WHERE first_batch_id = 'batch_03'
            """
        ).fetchone()[0]

        batch_03_duplicates = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_event_duplicates
            WHERE batch_id = 'batch_03'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert statuses == [
        ("batch_01", "completed"),
        ("batch_02", "completed"),
        ("batch_03", "failed"),
    ]
    assert batch_03_events == 0
    assert batch_03_duplicates == 0

    process_visit_state(db_path=db_path)

    connection = connect_database(read_only=True, db_path=db_path)
    try:
        final = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM visit_events),
                (SELECT COUNT(*) FROM visit_event_duplicates),
                (SELECT COUNT(*) FROM visit_current_state),
                (SELECT COUNT(*)
                 FROM ingestion_batches
                 WHERE status = 'completed')
            """
        ).fetchone()
    finally:
        connection.close()

    assert final == (1944, 35, 1618, 6)

def test_cumulative_report_contains_stage5_evidence(state_db, tmp_path):
    report_path = tmp_path / "Report.txt"
    connection = connect_database(read_only=True, db_path=state_db)

    try:
        write_processing_report(
            connection,
            report_path=report_path,
        )
    finally:
        connection.close()

    report = report_path.read_text(encoding="utf-8")

    assert "STAGE 5 - EVENT DEDUPLICATION AND CURRENT VISIT STATE" in report
    assert "Globally unique event IDs             : 1944" in report
    assert "Duplicate deliveries                  : 35" in report
    assert "Applied unique state changes          : 1890" in report
    assert "Stale/equal-version unique events     : 54" in report
    assert "Distinct current visit states         : 1618" in report
    assert "Active current visit states           : 1567" in report
    assert "Deleted current visit states          : 51" in report

    assert "Duplicate-delivery evidence:" in report
    assert "Stale/equal-version evidence:" in report
    assert "Deleted current-state tombstones:" in report

    assert "V02" in report
    assert "V03" in report
    assert "V04" in report
    assert "lower source_version" in report

