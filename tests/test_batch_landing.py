from __future__ import annotations

from pathlib import Path

import pytest

from src.database import connect_database
from src.land_visit_batches import land_visit_batches


EXPECTED_BATCHES = [
    ("batch_01", 1, "2026-04-13", 381),
    ("batch_02", 2, "2026-04-14", 323),
    ("batch_03", 3, "2026-04-15", 323),
    ("batch_04", 4, "2026-04-16", 323),
    ("batch_05", 5, "2026-04-17", 323),
    ("batch_06", 6, "2026-04-18", 324),
]


@pytest.fixture(scope="module")
def landed_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("stage3") / "stage3.duckdb"
    land_visit_batches(db_path=db_path)
    return db_path


def test_all_batches_are_landed_in_manifest_order(landed_db):
    connection = connect_database(read_only=True, db_path=landed_db)

    try:
        rows = connection.execute(
            """
            SELECT
                batch_id,
                manifest_order,
                CAST(snapshot_date AS VARCHAR),
                input_lines,
                status
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()
    finally:
        connection.close()

    expected = [
        (batch_id, order, snapshot_date, line_count, "landed")
        for batch_id, order, snapshot_date, line_count in EXPECTED_BATCHES
    ]

    assert rows == expected


def test_all_1997_physical_lines_are_preserved(landed_db):
    connection = connect_database(read_only=True, db_path=landed_db)

    try:
        total = connection.execute(
            "SELECT COUNT(*) FROM raw_visit_lines"
        ).fetchone()[0]
    finally:
        connection.close()

    assert total == 1997


def test_each_batch_has_expected_raw_line_count(landed_db):
    connection = connect_database(read_only=True, db_path=landed_db)

    try:
        actual = dict(
            connection.execute(
                """
                SELECT batch_id, COUNT(*)
                FROM raw_visit_lines
                GROUP BY batch_id
                ORDER BY batch_id
                """
            ).fetchall()
        )
    finally:
        connection.close()

    expected = {
        batch_id: line_count
        for batch_id, _order, _snapshot_date, line_count in EXPECTED_BATCHES
    }

    assert actual == expected


def test_line_numbers_are_contiguous_within_each_batch(landed_db):
    connection = connect_database(read_only=True, db_path=landed_db)

    try:
        rows = connection.execute(
            """
            SELECT
                batch_id,
                MIN(line_number) AS min_line,
                MAX(line_number) AS max_line,
                COUNT(*) AS row_count,
                COUNT(DISTINCT line_number) AS distinct_line_count
            FROM raw_visit_lines
            GROUP BY batch_id
            ORDER BY batch_id
            """
        ).fetchall()
    finally:
        connection.close()

    for batch_id, min_line, max_line, row_count, distinct_line_count in rows:
        assert min_line == 1
        assert max_line == row_count
        assert distinct_line_count == row_count
        assert batch_id.startswith("batch_")


def test_replaying_landed_batches_is_a_true_no_op(landed_db):
    connection = connect_database(read_only=True, db_path=landed_db)

    try:
        before = connection.execute(
            """
            SELECT
                batch_id,
                status,
                input_lines,
                attempt_count,
                first_started_at,
                last_attempt_at,
                landed_at,
                source_sha256
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()

        raw_before = connection.execute(
            "SELECT COUNT(*) FROM raw_visit_lines"
        ).fetchone()[0]
    finally:
        connection.close()

    replay_results = land_visit_batches(db_path=landed_db)

    connection = connect_database(read_only=True, db_path=landed_db)
    try:
        after = connection.execute(
            """
            SELECT
                batch_id,
                status,
                input_lines,
                attempt_count,
                first_started_at,
                last_attempt_at,
                landed_at,
                source_sha256
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()

        raw_after = connection.execute(
            "SELECT COUNT(*) FROM raw_visit_lines"
        ).fetchone()[0]
    finally:
        connection.close()

    assert all(result.action == "skipped" for result in replay_results)
    assert before == after
    assert raw_before == raw_after == 1997


def test_failed_batch_can_restart_without_partial_landing(tmp_path):
    db_path = tmp_path / "restart.duckdb"

    with pytest.raises(RuntimeError, match="Simulated landing failure"):
        land_visit_batches(
            db_path=db_path,
            fail_batch_id="batch_03",
            fail_after_lines=10,
        )

    connection = connect_database(read_only=True, db_path=db_path)

    try:
        statuses_after_failure = connection.execute(
            """
            SELECT batch_id, status, attempt_count
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()

        batch_03_raw_rows = connection.execute(
            """
            SELECT COUNT(*)
            FROM raw_visit_lines
            WHERE batch_id = 'batch_03'
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert statuses_after_failure == [
        ("batch_01", "landed", 1),
        ("batch_02", "landed", 1),
        ("batch_03", "failed", 1),
    ]
    assert batch_03_raw_rows == 0

    land_visit_batches(db_path=db_path)

    connection = connect_database(read_only=True, db_path=db_path)

    try:
        final_statuses = connection.execute(
            """
            SELECT batch_id, status, attempt_count, input_lines
            FROM ingestion_batches
            ORDER BY manifest_order
            """
        ).fetchall()

        total_raw_rows = connection.execute(
            "SELECT COUNT(*) FROM raw_visit_lines"
        ).fetchone()[0]
    finally:
        connection.close()

    assert final_statuses == [
        ("batch_01", "landed", 1, 381),
        ("batch_02", "landed", 1, 323),
        ("batch_03", "landed", 2, 323),
        ("batch_04", "landed", 1, 323),
        ("batch_05", "landed", 1, 323),
        ("batch_06", "landed", 1, 324),
    ]
    assert total_raw_rows == 1997
