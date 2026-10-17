from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from src.audit_report import (
    fetch_technical_rejects,
    format_reject_summary_table,
    format_text_table,
    write_processing_report,
)
from src.database import DB_PATH, apply_schema, connect_database


UPSERT_PAYLOAD_FIELDS = (
    "property_id",
    "contractor_id",
    "visitor_id",
    "started_at",
    "ended_at",
)


@dataclass(frozen=True)
class EventValidationResult:
    """Result of technical validation for one raw JSONL line."""

    is_valid: bool
    event: dict[str, Any] | None
    reason_codes: tuple[str, ...]
    reason_message: str


@dataclass(frozen=True)
class BatchValidationResult:
    """Small result object used for logging and tests."""

    batch_id: str
    action: str
    valid_lines: int
    invalid_lines: int


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_aware_timestamp(value: Any) -> datetime | None:
    """
    Parse ISO 8601 only when an explicit timezone/UTC offset is present.
    """
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None

    return parsed


def validate_event_payload(raw_payload: str) -> EventValidationResult:
    """
    Validate one visit-change line against the supplied technical rules.

    Business-quality checks such as unknown property/contractor references or
    end-before-start are deliberately deferred to a later stage.
    """
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        return EventValidationResult(
            is_valid=False,
            event=None,
            reason_codes=("malformed_json",),
            reason_message=f"Malformed JSON: {exc.msg}",
        )

    if not isinstance(payload, dict):
        return EventValidationResult(
            is_valid=False,
            event=None,
            reason_codes=("json_not_object",),
            reason_message="JSON value must be an object.",
        )

    reasons: list[tuple[str, str]] = []

    if not _is_nonempty_string(payload.get("event_id")):
        reasons.append(
            ("invalid_event_id", "event_id must be a nonempty string")
        )

    if not _is_nonempty_string(payload.get("visit_id")):
        reasons.append(
            ("invalid_visit_id", "visit_id must be a nonempty string")
        )

    source_version = payload.get("source_version")
    if (
        isinstance(source_version, bool)
        or not isinstance(source_version, int)
        or source_version <= 0
    ):
        reasons.append(
            (
                "invalid_source_version",
                "source_version must be a positive integer and not a boolean",
            )
        )

    operation = payload.get("operation")
    if operation not in {"upsert", "delete"}:
        reasons.append(
            ("invalid_operation", "operation must be 'upsert' or 'delete'")
        )

    updated_at = _parse_aware_timestamp(payload.get("updated_at"))
    if updated_at is None:
        reasons.append(
            (
                "invalid_updated_at",
                "updated_at must be a timezone-aware ISO 8601 timestamp",
            )
        )

    parsed_event: dict[str, Any] = {
        "event_id": payload.get("event_id"),
        "visit_id": payload.get("visit_id"),
        "source_version": source_version,
        "operation": operation,
        "updated_at": updated_at,
        "property_id": None,
        "contractor_id": None,
        "visitor_id": None,
        "started_at": None,
        "ended_at": None,
    }

    if operation == "upsert":
        for field_name in ("property_id", "contractor_id", "visitor_id"):
            value = payload.get(field_name)
            if not _is_nonempty_string(value):
                reasons.append(
                    (
                        f"invalid_{field_name}",
                        f"{field_name} must be a nonempty string for upserts",
                    )
                )
            else:
                parsed_event[field_name] = value.strip()

        started_at = _parse_aware_timestamp(payload.get("started_at"))
        if started_at is None:
            reasons.append(
                (
                    "invalid_started_at",
                    "started_at must be a timezone-aware ISO 8601 timestamp "
                    "for upserts",
                )
            )
        else:
            parsed_event["started_at"] = started_at

        if "ended_at" not in payload:
            reasons.append(
                (
                    "missing_ended_at",
                    "ended_at is required for upserts and may be null",
                )
            )
        elif payload["ended_at"] is None:
            parsed_event["ended_at"] = None
        else:
            ended_at = _parse_aware_timestamp(payload["ended_at"])
            if ended_at is None:
                reasons.append(
                    (
                        "invalid_ended_at",
                        "ended_at must be null or a timezone-aware "
                        "ISO 8601 timestamp",
                    )
                )
            else:
                parsed_event["ended_at"] = ended_at

    elif operation == "delete":
        unexpected_fields = [
            field_name
            for field_name in UPSERT_PAYLOAD_FIELDS
            if field_name in payload
        ]
        if unexpected_fields:
            reasons.append(
                (
                    "delete_contains_payload",
                    "delete events must omit visit payload fields: "
                    + ", ".join(unexpected_fields),
                )
            )

    if reasons:
        return EventValidationResult(
            is_valid=False,
            event=None,
            reason_codes=tuple(code for code, _ in reasons),
            reason_message="; ".join(message for _, message in reasons),
        )

    parsed_event["event_id"] = str(parsed_event["event_id"]).strip()
    parsed_event["visit_id"] = str(parsed_event["visit_id"]).strip()

    return EventValidationResult(
        is_valid=True,
        event=parsed_event,
        reason_codes=(),
        reason_message="",
    )


def _validate_one_batch(
    connection: duckdb.DuckDBPyConnection,
    batch_id: str,
) -> BatchValidationResult:
    batch_row = connection.execute(
        """
        SELECT status, validated_at
        FROM ingestion_batches
        WHERE batch_id = ?
        """,
        [batch_id],
    ).fetchone()

    if batch_row is None:
        raise ValueError(f"Unknown batch_id: {batch_id}")

    status, validated_at = batch_row

    if status == "completed" or validated_at is not None:
        counts = connection.execute(
            """
            SELECT technically_valid_lines, technically_invalid_lines
            FROM ingestion_batches
            WHERE batch_id = ?
            """,
            [batch_id],
        ).fetchone()

        return BatchValidationResult(
            batch_id=batch_id,
            action="skipped",
            valid_lines=int(counts[0]),
            invalid_lines=int(counts[1]),
        )

    if status != "landed":
        raise RuntimeError(
            f"{batch_id} is in status {status!r}; "
            "run visit batch landing before technical validation"
        )

    rows = connection.execute(
        """
        SELECT line_number, raw_payload
        FROM raw_visit_lines
        WHERE batch_id = ?
        ORDER BY line_number
        """,
        [batch_id],
    ).fetchall()

    valid_count = 0
    invalid_count = 0

    try:
        connection.execute("BEGIN TRANSACTION")

        connection.execute(
            "DELETE FROM validated_visit_events WHERE batch_id = ?",
            [batch_id],
        )
        connection.execute(
            "DELETE FROM visit_event_rejects WHERE batch_id = ?",
            [batch_id],
        )

        for line_number, raw_payload in rows:
            result = validate_event_payload(raw_payload)

            if result.is_valid:
                assert result.event is not None
                event = result.event

                connection.execute(
                    """
                    INSERT INTO validated_visit_events (
                        batch_id,
                        line_number,
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
                        raw_payload
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        batch_id,
                        line_number,
                        event["event_id"],
                        event["visit_id"],
                        event["source_version"],
                        event["operation"],
                        event["updated_at"],
                        event["property_id"],
                        event["contractor_id"],
                        event["visitor_id"],
                        event["started_at"],
                        event["ended_at"],
                        raw_payload,
                    ],
                )
                valid_count += 1
                continue

            connection.execute(
                """
                INSERT INTO visit_event_rejects (
                    batch_id,
                    line_number,
                    raw_payload,
                    reason_codes,
                    rejection_reason
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    batch_id,
                    line_number,
                    raw_payload,
                    ",".join(result.reason_codes),
                    result.reason_message,
                ],
            )
            invalid_count += 1

        connection.execute(
            """
            UPDATE ingestion_batches
            SET
                technically_valid_lines = ?,
                technically_invalid_lines = ?,
                validated_at = CURRENT_TIMESTAMP,
                error_message = NULL
            WHERE batch_id = ?
            """,
            [valid_count, invalid_count, batch_id],
        )

        connection.execute("COMMIT")

    except Exception as exc:
        connection.execute("ROLLBACK")
        connection.execute(
            """
            UPDATE ingestion_batches
            SET
                status = 'failed',
                validated_at = NULL,
                technically_valid_lines = 0,
                technically_invalid_lines = 0,
                error_message = ?
            WHERE batch_id = ?
            """,
            [str(exc), batch_id],
        )
        raise

    return BatchValidationResult(
        batch_id=batch_id,
        action="validated",
        valid_lines=valid_count,
        invalid_lines=invalid_count,
    )


def validate_visit_events(
    *,
    db_path: Path | str | None = None,
) -> list[BatchValidationResult]:
    """Technically validate all landed visit batches in manifest order."""
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
                "No landed visit batches found. "
                "Run: python -m src.land_visit_batches"
            )

        return [
            _validate_one_batch(connection, batch_id)
            for batch_id in batch_ids
        ]

    finally:
        connection.close()


def main() -> None:
    results = validate_visit_events()

    connection = connect_database(read_only=True)
    try:
        valid_total = connection.execute(
            "SELECT COUNT(*) FROM validated_visit_events"
        ).fetchone()[0]
        reject_total = connection.execute(
            "SELECT COUNT(*) FROM visit_event_rejects"
        ).fetchone()[0]

        rejects = fetch_technical_rejects(connection)
        report_path = write_processing_report(connection)
    finally:
        connection.close()

    print("=" * 112)
    print("VISIT EVENT TECHNICAL VALIDATION")
    print("=" * 112)

    print(
        format_text_table(
            headers=["Batch", "Action", "Valid", "Invalid"],
            rows=[
                (
                    result.batch_id,
                    result.action,
                    result.valid_lines,
                    result.invalid_lines,
                )
                for result in results
            ],
            widths=[10, 10, 8, 8],
        )
    )

    print()
    print("OVERALL TOTALS")
    print("-" * 112)
    print(f"Technically valid event lines   : {valid_total}")
    print(f"Technically invalid event lines : {reject_total}")

    print()
    print("REJECTED RECORDS")
    print("-" * 112)
    print(format_reject_summary_table(rejects))

    print()
    print(
        "Note: full reason codes, raw source records and source references are "
        "written to reports/Report.txt."
    )
    print(f"Cumulative audit report: {report_path}")
    print(
        "SUCCESS: Technical validation is complete; invalid lines remain auditable."
    )


if __name__ == "__main__":
    main()
