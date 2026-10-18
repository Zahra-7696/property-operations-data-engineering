from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.audit_report import format_text_table, write_processing_report
from src.database import apply_schema, connect_database


@dataclass(frozen=True)
class ManagementHistoryResult:
    """Summary of the reconstructed property-management history."""

    source_observations: int
    intervals: int
    collapsed_unchanged_observations: int
    properties: int
    current_management_rows: int


def _format_utc(value: datetime | None) -> str:
    if value is None:
        return "CURRENT"

    return (
        value.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def build_management_history(
    *,
    db_path: Path | str | None = None,
) -> ManagementHistoryResult:
    """
    Rebuild contiguous property-management intervals from source observations.

    Consecutive unchanged company observations collapse into one interval.
    A return to a previous company after an intervening company creates a new
    interval. Intervals use [valid_from, valid_to) semantics.
    """
    connection = connect_database(db_path=db_path)
    apply_schema(connection)

    try:
        source_observations = connection.execute(
            "SELECT COUNT(*) FROM src_property_management"
        ).fetchone()[0]

        if source_observations == 0:
            raise RuntimeError(
                "No property-management source observations found. "
                "Run: python -m src.load_reference_data"
            )

        connection.execute("BEGIN TRANSACTION")
        connection.execute("DELETE FROM property_management_intervals")

        connection.execute(
            """
            INSERT INTO property_management_intervals (
                property_id,
                company_id,
                valid_from,
                valid_to,
                start_observation_id,
                source_observation_count
            )
            WITH ordered AS (
                SELECT
                    observation_id,
                    property_id,
                    company_id,
                    effective_at,
                    LAG(company_id) OVER (
                        PARTITION BY property_id
                        ORDER BY effective_at, observation_id
                    ) AS previous_company
                FROM src_property_management
            ),
            marked AS (
                SELECT
                    *,
                    CASE
                        WHEN previous_company IS NULL
                          OR company_id <> previous_company
                        THEN 1
                        ELSE 0
                    END AS starts_new_interval
                FROM ordered
            ),
            grouped AS (
                SELECT
                    *,
                    SUM(starts_new_interval) OVER (
                        PARTITION BY property_id
                        ORDER BY effective_at, observation_id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS interval_group
                FROM marked
            ),
            collapsed AS (
                SELECT
                    property_id,
                    company_id,
                    interval_group,
                    MIN(effective_at) AS valid_from,
                    FIRST(
                        observation_id
                        ORDER BY effective_at, observation_id
                    ) AS start_observation_id,
                    COUNT(*) AS source_observation_count
                FROM grouped
                GROUP BY
                    property_id,
                    company_id,
                    interval_group
            ),
            bounded AS (
                SELECT
                    property_id,
                    company_id,
                    valid_from,
                    LEAD(valid_from) OVER (
                        PARTITION BY property_id
                        ORDER BY valid_from, start_observation_id
                    ) AS valid_to,
                    start_observation_id,
                    source_observation_count
                FROM collapsed
            )
            SELECT
                property_id,
                company_id,
                valid_from,
                valid_to,
                start_observation_id,
                source_observation_count
            FROM bounded
            ORDER BY property_id, valid_from
            """
        )

        interval_count = connection.execute(
            "SELECT COUNT(*) FROM property_management_intervals"
        ).fetchone()[0]

        mapped_observations = connection.execute(
            """
            SELECT COALESCE(SUM(source_observation_count), 0)
            FROM property_management_intervals
            """
        ).fetchone()[0]

        if mapped_observations != source_observations:
            raise ValueError(
                "Management interval rebuild did not account for every "
                "source observation"
            )

        invalid_bounds = connection.execute(
            """
            SELECT COUNT(*)
            FROM property_management_intervals
            WHERE valid_to IS NOT NULL
              AND valid_to <= valid_from
            """
        ).fetchone()[0]

        if invalid_bounds:
            raise ValueError(
                "Management history contains non-positive interval bounds"
            )

        properties = connection.execute(
            """
            SELECT COUNT(DISTINCT property_id)
            FROM property_management_intervals
            """
        ).fetchone()[0]

        current_rows = connection.execute(
            "SELECT COUNT(*) FROM current_property_management"
        ).fetchone()[0]

        if current_rows != properties:
            raise ValueError(
                "Each property must have exactly one current management row"
            )

        connection.execute("COMMIT")

        return ManagementHistoryResult(
            source_observations=int(source_observations),
            intervals=int(interval_count),
            collapsed_unchanged_observations=int(
                source_observations - interval_count
            ),
            properties=int(properties),
            current_management_rows=int(current_rows),
        )

    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise

    finally:
        connection.close()



def _boundary_examples(
    connection: duckdb.DuckDBPyConnection,
) -> list[tuple[object, ...]]:
    """
    Return real supplied P03 records around the management transfer boundary.

    Visits are included when Stage 5 state is present. Incidents come from the
    Stage 2 reference layer.
    """
    examples: list[tuple[object, ...]] = []

    visit_rows = connection.execute(
        """
        SELECT visit_id, property_id, started_at
        FROM visit_current_state
        WHERE visit_id IN ('V09', 'V10')
        ORDER BY started_at, visit_id
        """
    ).fetchall()

    for visit_id, property_id, event_time in visit_rows:
        company = connection.execute(
            """
            SELECT company_id
            FROM property_management_intervals
            WHERE property_id = ?
              AND ? >= valid_from
              AND (valid_to IS NULL OR ? < valid_to)
            """,
            [property_id, event_time, event_time],
        ).fetchone()

        examples.append(
            (
                "visit",
                visit_id,
                property_id,
                _format_utc(event_time),
                None if company is None else company[0],
            )
        )

    incident_rows = connection.execute(
        """
        SELECT incident_id, property_id, reported_at
        FROM src_incidents
        WHERE incident_id IN ('I06', 'I07')
        ORDER BY reported_at, incident_id
        """
    ).fetchall()

    for incident_id, property_id, event_time in incident_rows:
        company = connection.execute(
            """
            SELECT company_id
            FROM property_management_intervals
            WHERE property_id = ?
              AND ? >= valid_from
              AND (valid_to IS NULL OR ? < valid_to)
            """,
            [property_id, event_time, event_time],
        ).fetchone()

        examples.append(
            (
                "incident",
                incident_id,
                property_id,
                _format_utc(event_time),
                None if company is None else company[0],
            )
        )

    return sorted(examples, key=lambda row: (row[3], row[0], row[1]))


def main() -> None:
    result = build_management_history()

    connection = connect_database(read_only=True)
    try:

        all_intervals = connection.execute(
            """
            SELECT
                i.property_id,
                p.property_name,
                i.company_id,
                c.company_name,
                i.valid_from,
                i.valid_to,
                i.start_observation_id,
                i.source_observation_count
            FROM property_management_intervals AS i
            JOIN src_properties AS p
                ON p.property_id = i.property_id
            JOIN src_companies AS c
                ON c.company_id = i.company_id
            ORDER BY i.property_id, i.valid_from
            """
        ).fetchall()

        p01_history = connection.execute(
            """
            SELECT
                company_id,
                valid_from,
                valid_to,
                source_observation_count
            FROM property_management_intervals
            WHERE property_id = 'P01'
            ORDER BY valid_from
            """
        ).fetchall()

        boundaries = _boundary_examples(connection)

        current_rows = connection.execute(
            """
            SELECT
                property_id,
                property_name,
                company_id,
                company_name,
                managed_from
            FROM current_property_management
            ORDER BY property_id
            """
        ).fetchall()

        report_path = write_processing_report(connection)
    finally:
        connection.close()

    print("=" * 118)
    print("PROPERTY MANAGEMENT HISTORY")
    print("=" * 118)

    print("OVERALL TOTALS")
    print("-" * 118)
    print(f"Source management observations      : {result.source_observations}")
    print(f"Historical management intervals     : {result.intervals}")
    print(
        "Unchanged observations collapsed   : "
        f"{result.collapsed_unchanged_observations}"
    )
    print(f"Properties represented              : {result.properties}")
    print(f"Current management rows             : {result.current_management_rows}")


    print()
    print("ALL RECONSTRUCTED MANAGEMENT INTERVALS")
    print("-" * 118)
    print(
        format_text_table(
            headers=[
                "Property",
                "Property name",
                "Company",
                "Company name",
                "Valid from UTC",
                "Valid to UTC",
                "Start obs",
                "Obs count",
            ],
            rows=[
                (
                    property_id,
                    property_name,
                    company_id,
                    company_name,
                    _format_utc(valid_from),
                    _format_utc(valid_to),
                    start_observation_id,
                    observation_count,
                )
                for (
                    property_id,
                    property_name,
                    company_id,
                    company_name,
                    valid_from,
                    valid_to,
                    start_observation_id,
                    observation_count,
                ) in all_intervals
            ],
            widths=[9, 23, 8, 25, 20, 20, 11, 9],
        )
    )

    print()
    print("P01 RETURN-TO-PREVIOUS-MANAGER EXAMPLE")
    print("-" * 118)
    print(
        format_text_table(
            headers=[
                "Company",
                "Valid from UTC",
                "Valid to UTC",
                "Source observations",
            ],
            rows=[
                (
                    company_id,
                    _format_utc(valid_from),
                    _format_utc(valid_to),
                    observation_count,
                )
                for (
                    company_id,
                    valid_from,
                    valid_to,
                    observation_count,
                ) in p01_history
            ],
            widths=[10, 22, 22, 20],
        )
    )

    print()
    print("P03 TRANSFER-BOUNDARY EVIDENCE")
    print("-" * 118)
    print(
        format_text_table(
            headers=["Type", "Record", "Property", "Business time UTC", "Company"],
            rows=boundaries,
            widths=[10, 10, 10, 22, 10],
        )
    )

    print()
    print("CURRENT MANAGEMENT LOOKUP")
    print("-" * 118)
    print(
        format_text_table(
            headers=[
                "Property",
                "Property name",
                "Company",
                "Company name",
                "Managed from UTC",
            ],
            rows=[
                (
                    property_id,
                    property_name,
                    company_id,
                    company_name,
                    _format_utc(managed_from),
                )
                for (
                    property_id,
                    property_name,
                    company_id,
                    company_name,
                    managed_from,
                ) in current_rows
            ],
            widths=[10, 24, 10, 26, 22],
        )
    )

    print()
    print(
        "Rule: management intervals are [valid_from, valid_to): "
        "the start is included and the end is excluded."
    )
    print(
        "Rule: an event exactly at a transfer timestamp belongs to the new company."
    )
    print(f"Cumulative audit report: {report_path}")
    print("SUCCESS: Property-management history and current lookup are complete.")


if __name__ == "__main__":
    main()
