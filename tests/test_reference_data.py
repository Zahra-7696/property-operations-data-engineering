from __future__ import annotations

import pytest

from src.database import connect_database


EXPECTED_COUNTS = {
    "src_companies": 3,
    "src_contractors": 8,
    "src_properties": 24,
    "src_property_management": 62,
    "src_incidents": 247,
    "src_report_weeks": 6,
}


@pytest.fixture(scope="module")
def connection():
    con = connect_database(read_only=True)
    yield con
    con.close()


@pytest.mark.parametrize(
    ("table_name", "expected_count"),
    EXPECTED_COUNTS.items(),
)
def test_reference_table_counts(connection, table_name, expected_count):
    actual = connection.execute(
        f"SELECT COUNT(*) FROM {table_name}"
    ).fetchone()[0]

    assert actual == expected_count


def test_property_management_references_known_properties(connection):
    count = connection.execute(
        """
        SELECT COUNT(*)
        FROM src_property_management AS m
        LEFT JOIN src_properties AS p
            ON p.property_id = m.property_id
        WHERE p.property_id IS NULL
        """
    ).fetchone()[0]

    assert count == 0


def test_property_management_references_known_companies(connection):
    count = connection.execute(
        """
        SELECT COUNT(*)
        FROM src_property_management AS m
        LEFT JOIN src_companies AS c
            ON c.company_id = m.company_id
        WHERE c.company_id IS NULL
        """
    ).fetchone()[0]

    assert count == 0


def test_incidents_reference_known_properties(connection):
    count = connection.execute(
        """
        SELECT COUNT(*)
        FROM src_incidents AS i
        LEFT JOIN src_properties AS p
            ON p.property_id = i.property_id
        WHERE p.property_id IS NULL
        """
    ).fetchone()[0]

    assert count == 0


def test_incident_severity_domain(connection):
    unexpected = connection.execute(
        """
        SELECT DISTINCT severity
        FROM src_incidents
        WHERE severity NOT IN ('low', 'medium', 'high')
        """
    ).fetchall()

    assert unexpected == []


def test_requested_reporting_weeks_are_mondays(connection):
    non_mondays = connection.execute(
        """
        SELECT week_start
        FROM src_report_weeks
        WHERE EXTRACT(DOW FROM week_start) <> 1
        """
    ).fetchall()

    assert non_mondays == []
