from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from src.audit_report import (
    fetch_duplicate_deliveries,
    fetch_stale_events,
    format_duplicate_delivery_table,
    format_stale_event_table,
    format_text_table,
    write_processing_report,
)
from src.database import DB_PATH, apply_schema, connect_database


@dataclass(frozen=True)
class BatchStateResult:
    """Processing summary for one validated visit-change batch."""

    batch_id: str
    action: str
    unique_events: int
    duplicate_events: int
    applied_state_changes: int
    stale_events: int


def _event_signature(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return the business/event fields used to compare repeated event IDs."""
    # Row layout:
    # event_id, visit_id, source_version, operation, updated_at,
    # property_id, contractor_id, visitor_id, started_at, ended_at,
    # batch_id, line_number, raw_payload
    return row[1:10]


def _current_state_signature(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return fields used to verify equal-version states are consistent."""
    # Row layout:
    # source_version, operation, updated_at,
    # property_id, contractor_id, visitor_id, started_at, ended_at
    return row[1:]


def _incoming_state_signature(event_row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return an incoming event state signature excluding event identity."""
    # event row indexes:
    # 0 event_id
    # 1 visit_id
    # 2 source_version
    # 3 operation
    # 4 updated_at
    # 5 property_id
    # 6 contractor_id
    # 7 visitor_id
    # 8 started_at
    # 9 ended_at
    return (
        event_row[3],
        event_row[4],
        event_row[5],
        event_row[6],
        event_row[7],
        event_row[8],
        event_row[9],
    )


def _assert_duplicate_event_matches(
    connection: duckdb.DuckDBPyConnection,
    incoming_row: tuple[Any, ...],
    canonical_row: tuple[Any, ...],
) -> None:
    """
    Guard the source guarantee that repeated event IDs have identical content.

    A duplicate event delivery is safe only when the already-stored event has the
    same visit/version/operation/timestamps/payload fields.
    """
    if _event_signature(incoming_row) != _event_signature(canonical_row):
        event_id = incoming_row[0]
        raise ValueError(
            f"Conflicting payloads found for repeated event_id {event_id!r}"
        )


def _upsert_current_state(
    connection: duckdb.DuckDBPyConnection,
    *,
    event_row: tuple[Any, ...],
) -> None:
    (
        event_id,
        visit_id,
        source_version,
        operation,
        updated_at,
        property_id,
        contractor_id,
        visitor_id,
        started_at,
        ended_at,
        batch_id,
        line_number,
        _raw_payload,
    ) = event_row

    is_deleted = operation == "delete"

    connection.execute(
        """
        INSERT INTO visit_current_state (
            visit_id,
            source_version,
            operation,
            is_deleted,
            winning_event_id,
            updated_at,
            property_id,
            contractor_id,
            visitor_id,
            started_at,
            ended_at,
            source_batch_id,
            source_line_number
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (visit_id) DO UPDATE SET
            source_version = EXCLUDED.source_version,
            operation = EXCLUDED.operation,
            is_deleted = EXCLUDED.is_deleted,
            winning_event_id = EXCLUDED.winning_event_id,
            updated_at = EXCLUDED.updated_at,
            property_id = EXCLUDED.property_id,
            contractor_id = EXCLUDED.contractor_id,
            visitor_id = EXCLUDED.visitor_id,
            started_at = EXCLUDED.started_at,
            ended_at = EXCLUDED.ended_at,
            source_batch_id = EXCLUDED.source_batch_id,
            source_line_number = EXCLUDED.source_line_number
        """,
        [
            visit_id,
            source_version,
            operation,
            is_deleted,
            event_id,
            updated_at,
            property_id,
            contractor_id,
            visitor_id,
            started_at,
            ended_at,
            batch_id,
            line_number,
        ],
    )


def _process_one_batch(
    connection: duckdb.DuckDBPyConnection,
    batch_id: str,
    *,
    fail_after_unique_events: int | None = None,
) -> BatchStateResult:
    batch_row = connection.execute(
        """
        SELECT
            status,
            validated_at,
            unique_event_lines,
            duplicate_event_lines,
            applied_state_changes,
            stale_event_lines
        FROM ingestion_batches
        WHERE batch_id = ?
        """,
        [batch_id],
    ).fetchone()

    if batch_row is None:
        raise ValueError(f"Unknown batch_id: {batch_id}")

    (
        status,
        validated_at,
        existing_unique,
        existing_duplicates,
        existing_applied,
        existing_stale,
    ) = batch_row

    if status == "completed":
        return BatchStateResult(
            batch_id=batch_id,
            action="skipped",
            unique_events=int(existing_unique),
            duplicate_events=int(existing_duplicates),
            applied_state_changes=int(existing_applied),
            stale_events=int(existing_stale),
        )

    if validated_at is None:
        raise RuntimeError(
            f"{batch_id} has not completed technical validation. "
            "Run: python -m src.validate_visit_events"
        )

    if status not in {"landed", "failed"}:
        raise RuntimeError(
            f"{batch_id} cannot be state-processed from status {status!r}"
        )

    event_rows = connection.execute(
        """
        SELECT
            event_id,
            visit_id,
            source_version,
            operation,
            updated_at,
            property_id,
            contractor_id,
            visitor_id,
            started_at,
            ended_at,
            batch_id,
            line_number,
            raw_payload
        FROM validated_visit_events
        WHERE batch_id = ?
        ORDER BY line_number
        """,
        [batch_id],
    ).fetchall()

    unique_count = 0
    duplicate_count = 0
    applied_count = 0
    stale_count = 0

    try:
        connection.execute("BEGIN TRANSACTION")

        for event_row in event_rows:
            (
                event_id,
                visit_id,
                source_version,
                operation,
                updated_at,
                property_id,
                contractor_id,
                visitor_id,
                started_at,
                ended_at,
                _batch_id,
                line_number,
                raw_payload,
            ) = event_row

            canonical = connection.execute(
                """
                SELECT
                    event_id,
                    visit_id,
                    source_version,
                    operation,
                    updated_at,
                    property_id,
                    contractor_id,
                    visitor_id,
                    started_at,
                    ended_at,
                    first_batch_id,
                    first_line_number,
                    raw_payload
                FROM visit_events
                WHERE event_id = ?
                """,
                [event_id],
            ).fetchone()

            if canonical is not None:
                _assert_duplicate_event_matches(
                    connection,
                    event_row,
                    canonical,
                )

                connection.execute(
                    """
                    INSERT INTO visit_event_duplicates (
                        batch_id,
                        line_number,
                        event_id,
                        canonical_batch_id,
                        canonical_line_number
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        batch_id,
                        line_number,
                        event_id,
                        canonical[10],
                        canonical[11],
                    ],
                )
                duplicate_count += 1
                continue

            current = connection.execute(
                """
                SELECT
                    source_version,
                    operation,
                    updated_at,
                    property_id,
                    contractor_id,
                    visitor_id,
                    started_at,
                    ended_at
                FROM visit_current_state
                WHERE visit_id = ?
                """,
                [visit_id],
            ).fetchone()

            prior_source_version = None if current is None else int(current[0])

            if current is None or source_version > current[0]:
                state_action = "applied"
                _upsert_current_state(connection, event_row=event_row)
                applied_count += 1

            else:
                state_action = "stale"
                stale_count += 1

                # Equal versions are guaranteed not to conflict. Make that
                # guarantee executable so a future source change cannot silently
                # create an ambiguous current state.
                if source_version == current[0]:
                    if (
                        _incoming_state_signature(event_row)
                        != _current_state_signature(current)
                    ):
                        raise ValueError(
                            "Conflicting equal-version states found for "
                            f"visit_id {visit_id!r}, version {source_version}"
                        )

            connection.execute(
                """
                INSERT INTO visit_events (
                    event_id,
                    visit_id,
                    source_version,
                    operation,
                    updated_at,
                    property_id,
                    contractor_id,
                    visitor_id,
                    started_at,
                    ended_at,
                    first_batch_id,
                    first_line_number,
                    state_action,
                    prior_source_version,
                    raw_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event_id,
                    visit_id,
                    source_version,
                    operation,
                    updated_at,
                    property_id,
                    contractor_id,
                    visitor_id,
                    started_at,
                    ended_at,
                    batch_id,
                    line_number,
                    state_action,
                    prior_source_version,
                    raw_payload,
                ],
            )

            unique_count += 1

            if (
                fail_after_unique_events is not None
                and unique_count >= fail_after_unique_events
            ):
                raise RuntimeError(
                    f"Simulated state-processing failure for {batch_id} "
                    f"after {unique_count} unique events"
                )

        connection.execute(
            """
            UPDATE ingestion_batches
            SET
                status = 'completed',
                completed_at = CURRENT_TIMESTAMP,
                unique_event_lines = ?,
                duplicate_event_lines = ?,
                applied_state_changes = ?,
                stale_event_lines = ?,
                error_message = NULL
            WHERE batch_id = ?
            """,
            [
                unique_count,
                duplicate_count,
                applied_count,
                stale_count,
                batch_id,
            ],
        )

        connection.execute("COMMIT")

    except Exception as exc:
        connection.execute("ROLLBACK")

        connection.execute(
            """
            UPDATE ingestion_batches
            SET
                status = 'failed',
                completed_at = NULL,
                unique_event_lines = 0,
                duplicate_event_lines = 0,
                applied_state_changes = 0,
                stale_event_lines = 0,
                error_message = ?
            WHERE batch_id = ?
            """,
            [str(exc), batch_id],
        )
        raise

    return BatchStateResult(
        batch_id=batch_id,
        action="processed",
        unique_events=unique_count,
        duplicate_events=duplicate_count,
        applied_state_changes=applied_count,
        stale_events=stale_count,
    )


def process_visit_state(
    *,
    db_path: Path | str | None = None,
    fail_batch_id: str | None = None,
    fail_after_unique_events: int | None = None,
) -> list[BatchStateResult]:
    """
    Deduplicate event deliveries and maintain highest-version visit state.

    Batches are processed in manifest order. A completed batch is a strict no-op.
    Failure injection exists only for automated restart/rollback tests.
    """
    connection = connect_database(db_path=db_path)
    apply_schema(connection)

    try:
        batch_ids = [
            row[0]
            for row in connection.execute(
                """
                SELECT batch_id
                FROM ingestion_batches
                ORDER BY manifest_order
                """
            ).fetchall()
        ]

        if not batch_ids:
            raise RuntimeError(
                "No visit batches found. Run landing and validation first."
            )

        results: list[BatchStateResult] = []

        for batch_id in batch_ids:
            injected_failure = (
                batch_id == fail_batch_id
                and fail_after_unique_events is not None
            )

            result = _process_one_batch(
                connection,
                batch_id,
                fail_after_unique_events=(
                    fail_after_unique_events if injected_failure else None
                ),
            )
            results.append(result)

        return results

    finally:
        connection.close()


def main() -> None:
    results = process_visit_state()

    connection = connect_database(read_only=True)
    try:
        unique_events = connection.execute(
            "SELECT COUNT(*) FROM visit_events"
        ).fetchone()[0]

        duplicate_deliveries = connection.execute(
            "SELECT COUNT(*) FROM visit_event_duplicates"
        ).fetchone()[0]

        applied_events = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_events
            WHERE state_action = 'applied'
            """
        ).fetchone()[0]

        stale_events_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_events
            WHERE state_action = 'stale'
            """
        ).fetchone()[0]

        visit_states = connection.execute(
            "SELECT COUNT(*) FROM visit_current_state"
        ).fetchone()[0]

        active_visits = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_current_state
            WHERE is_deleted = FALSE
            """
        ).fetchone()[0]

        deleted_visits = connection.execute(
            """
            SELECT COUNT(*)
            FROM visit_current_state
            WHERE is_deleted = TRUE
            """
        ).fetchone()[0]

        duplicates = fetch_duplicate_deliveries(connection)
        stale_events = fetch_stale_events(connection)

        key_states_raw = connection.execute(
            """
            SELECT
                visit_id,
                source_version,
                operation,
                is_deleted,
                winning_event_id,
                source_batch_id,
                source_line_number
            FROM visit_current_state
            WHERE visit_id IN ('V02', 'V03', 'V04')
            ORDER BY visit_id
            """
        ).fetchall()

        key_states = [
            (
                visit_id,
                source_version,
                operation,
                is_deleted,
                winning_event_id,
                f"{batch_id}:{line_number}",
            )
            for (
                visit_id,
                source_version,
                operation,
                is_deleted,
                winning_event_id,
                batch_id,
                line_number,
            ) in key_states_raw
        ]

        report_path = write_processing_report(connection)
    finally:
        connection.close()

    print("=" * 116)
    print("VISIT EVENT DEDUPLICATION AND CURRENT STATE")
    print("=" * 116)

    print(
        format_text_table(
            headers=[
                "Batch",
                "Action",
                "Unique",
                "Duplicates",
                "Applied",
                "Stale",
            ],
            rows=[
                (
                    result.batch_id,
                    result.action,
                    result.unique_events,
                    result.duplicate_events,
                    result.applied_state_changes,
                    result.stale_events,
                )
                for result in results
            ],
            widths=[10, 10, 8, 10, 8, 7],
        )
    )

    print()
    print("OVERALL TOTALS")
    print("-" * 116)
    print(f"Unique event IDs             : {unique_events}")
    print(f"Duplicate deliveries        : {duplicate_deliveries}")
    print(f"Applied state changes       : {applied_events}")
    print(f"Stale/equal-version events  : {stale_events_count}")
    print(f"Distinct current visits     : {visit_states}")
    print(f"Active current visits       : {active_visits}")
    print(f"Deleted current visits      : {deleted_visits}")

    print()
    print("KEY VERSION / DELETE EXAMPLES")
    print("-" * 116)
    print(
        format_text_table(
            headers=[
                "Visit",
                "Version",
                "Operation",
                "Deleted",
                "Winning event",
                "Source",
            ],
            rows=key_states,
            widths=[8, 8, 10, 8, 14, 18],
        )
    )

    print()
    print("DUPLICATE DELIVERY DETAILS")
    print("-" * 116)
    print(format_duplicate_delivery_table(duplicates))

    print()
    print("STALE / EQUAL-VERSION EVENT DETAILS")
    print("-" * 116)
    print(format_stale_event_table(stale_events))

    print()
    print(
        "Note: complete Stage 1-5 evidence, including deleted tombstones, "
        "is written to reports/Report.txt."
    )
    print(f"Cumulative audit report: {report_path}")
    print(
        "SUCCESS: Event deliveries are deduplicated and current visit state "
        "uses the highest source_version per visit."
    )


if __name__ == "__main__":
    main()
