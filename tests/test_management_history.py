from __future__ import annotations

from pathlib import Path

import pytest

from src.audit_report import write_processing_report
from src.build_management_history import build_management_history
from src.database import connect_database
from src.load_reference_data import load_reference_data


@pytest.fixture(scope="module")
def management_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("stage7") / "stage7.duckdb"
    load_reference_data(db_path=db_path)
    build_management_history(db_path=db_path)
    return db_path


def test_expected_management_history_counts(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        source_observations = connection.execute(
            "SELECT COUNT(*) FROM src_property_management"
        ).fetchone()[0]

        interval_count = connection.execute(
            "SELECT COUNT(*) FROM property_management_intervals"
        ).fetchone()[0]

        mapped_observations = connection.execute(
            """
            SELECT SUM(source_observation_count)
            FROM property_management_intervals
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert source_observations == 62
    assert interval_count == 37
    assert mapped_observations == 62


def test_current_management_has_one_row_per_property(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        total, distinct_properties = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT property_id)
            FROM current_property_management
            """
        ).fetchone()
    finally:
        connection.close()

    assert total == 24
    assert distinct_properties == 24


def test_p01_return_to_previous_manager_is_not_merged(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        history = connection.execute(
            """
            SELECT
                company_id,
                CAST(valid_from AS VARCHAR),
                CAST(valid_to AS VARCHAR),
                source_observation_count
            FROM property_management_intervals
            WHERE property_id = 'P01'
            ORDER BY valid_from
            """
        ).fetchall()
    finally:
        connection.close()

    assert len(history) == 3
    assert [row[0] for row in history] == ["C01", "C02", "C01"]
    assert [row[3] for row in history] == [2, 2, 1]


def test_p03_transfer_boundary_is_start_inclusive_end_exclusive(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        before = connection.execute(
            """
            SELECT company_id
            FROM property_management_intervals
            WHERE property_id = 'P03'
              AND TIMESTAMPTZ '2026-03-29 12:59:00+00:00' >= valid_from
              AND (
                    valid_to IS NULL
                    OR TIMESTAMPTZ '2026-03-29 12:59:00+00:00' < valid_to
              )
            """
        ).fetchone()

        at_transfer = connection.execute(
            """
            SELECT company_id
            FROM property_management_intervals
            WHERE property_id = 'P03'
              AND TIMESTAMPTZ '2026-03-29 13:00:00+00:00' >= valid_from
              AND (
                    valid_to IS NULL
                    OR TIMESTAMPTZ '2026-03-29 13:00:00+00:00' < valid_to
              )
            """
        ).fetchone()
    finally:
        connection.close()

    assert before == ("C02",)
    assert at_transfer == ("C03",)


def test_incidents_on_p03_follow_transfer_boundary(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        actual = connection.execute(
            """
            SELECT
                i.incident_id,
                m.company_id
            FROM src_incidents AS i
            JOIN property_management_intervals AS m
                ON m.property_id = i.property_id
               AND i.reported_at >= m.valid_from
               AND (m.valid_to IS NULL OR i.reported_at < m.valid_to)
            WHERE i.incident_id IN ('I06', 'I07')
            ORDER BY i.incident_id
            """
        ).fetchall()
    finally:
        connection.close()

    assert actual == [
        ("I06", "C03"),
        ("I07", "C02"),
    ]


def test_all_closed_intervals_have_positive_duration(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        invalid = connection.execute(
            """
            SELECT COUNT(*)
            FROM property_management_intervals
            WHERE valid_to IS NOT NULL
              AND valid_to <= valid_from
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert invalid == 0


def test_only_current_interval_has_null_valid_to(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        null_intervals = connection.execute(
            """
            SELECT COUNT(*)
            FROM property_management_intervals
            WHERE valid_to IS NULL
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert null_intervals == 24


def test_current_management_lookup_includes_names(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        p03 = connection.execute(
            """
            SELECT
                property_id,
                property_name,
                company_id,
                company_name
            FROM current_property_management
            WHERE property_id = 'P03'
            """
        ).fetchone()
    finally:
        connection.close()

    assert p03[0] == "P03"
    assert p03[2] == "C03"
    assert p03[1]
    assert p03[3]


def test_management_history_rebuild_is_idempotent(management_db):
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        before = connection.execute(
            """
            SELECT *
            FROM property_management_intervals
            ORDER BY property_id, valid_from
            """
        ).fetchall()
    finally:
        connection.close()

    result = build_management_history(db_path=management_db)

    connection = connect_database(read_only=True, db_path=management_db)

    try:
        after = connection.execute(
            """
            SELECT *
            FROM property_management_intervals
            ORDER BY property_id, valid_from
            """
        ).fetchall()
    finally:
        connection.close()

    assert result.source_observations == 62
    assert result.intervals == 37
    assert result.current_management_rows == 24
    assert before == after


def test_cumulative_report_contains_stage7_evidence(management_db, tmp_path):
    report_path = tmp_path / "Report.txt"
    connection = connect_database(read_only=True, db_path=management_db)

    try:
        write_processing_report(connection, report_path=report_path)
    finally:
        connection.close()

    report = report_path.read_text(encoding="utf-8")

    assert "STAGE 7 - PROPERTY MANAGEMENT HISTORY" in report
    assert "Source management observations    : 62" in report
    assert "Historical management intervals   : 37" in report
    assert "Unchanged observations collapsed  : 25" in report
    assert "Current management rows           : 24" in report

    assert "P01 return-to-previous-manager proof:" in report
    assert "P03 transfer-boundary attribution proof:" in report
    assert "I06" in report
    assert "C03" in report
    assert "I07" in report
    assert "C02" in report
