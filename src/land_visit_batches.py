from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

from src.database import DB_PATH, PROJECT_ROOT, apply_schema, connect_database
from src.verify_sources import sha256_file


MANIFEST_PATH = PROJECT_ROOT / "manifest.json"


@dataclass(frozen=True)
class BatchSpec:
    """One ordered batch entry from manifest.json."""

    manifest_order: int
    batch_id: str
    relative_file: str
    source_path: Path
    snapshot_date: date


@dataclass(frozen=True)
class BatchLandingResult:
    """Small result object used for logging and tests."""

    batch_id: str
    action: str
    input_lines: int


def load_manifest(
    manifest_path: Path = MANIFEST_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
) -> list[BatchSpec]:
    """Read and validate manifest.json while preserving its declared order."""
    with manifest_path.open("r", encoding="utf-8") as file:
        raw_manifest = json.load(file)

    if not isinstance(raw_manifest, list) or not raw_manifest:
        raise ValueError("manifest.json must contain a non-empty JSON array")

    specs: list[BatchSpec] = []
    seen_batch_ids: set[str] = set()

    for manifest_order, item in enumerate(raw_manifest, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"Manifest entry {manifest_order} must be a JSON object"
            )

        try:
            batch_id = str(item["batch_id"]).strip()
            relative_file = str(item["file"]).strip()
            snapshot_date = date.fromisoformat(str(item["snapshot_date"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(
                f"Invalid manifest entry at position {manifest_order}: {item}"
            ) from exc

        if not batch_id:
            raise ValueError(
                f"Manifest entry {manifest_order} has an empty batch_id"
            )

        if batch_id in seen_batch_ids:
            raise ValueError(f"Duplicate batch_id in manifest: {batch_id}")

        source_path = project_root / relative_file
        if not source_path.exists():
            raise FileNotFoundError(
                f"Manifest batch {batch_id} points to a missing file: "
                f"{source_path}"
            )

        seen_batch_ids.add(batch_id)
        specs.append(
            BatchSpec(
                manifest_order=manifest_order,
                batch_id=batch_id,
                relative_file=relative_file,
                source_path=source_path,
                snapshot_date=snapshot_date,
            )
        )

    return specs


def _get_existing_batch(
    connection: duckdb.DuckDBPyConnection,
    batch_id: str,
):
    return connection.execute(
        """
        SELECT
            batch_id,
            source_file,
            snapshot_date,
            source_sha256,
            status,
            input_lines
        FROM ingestion_batches
        WHERE batch_id = ?
        """,
        [batch_id],
    ).fetchone()


def _assert_batch_identity_is_stable(
    *,
    spec: BatchSpec,
    source_hash: str,
    existing_row,
) -> None:
    """
    Reject reuse of a known batch ID with changed metadata or file contents.

    The assignment guarantees stable batch IDs/contents. Making that assumption
    executable protects idempotent replay from silently accepting changed input.
    """
    (
        _batch_id,
        existing_file,
        existing_snapshot_date,
        existing_hash,
        _status,
        _input_lines,
    ) = existing_row

    if existing_file != spec.relative_file:
        raise ValueError(
            f"{spec.batch_id}: source file changed from "
            f"{existing_file!r} to {spec.relative_file!r}"
        )

    if existing_snapshot_date != spec.snapshot_date:
        raise ValueError(
            f"{spec.batch_id}: snapshot_date changed from "
            f"{existing_snapshot_date} to {spec.snapshot_date}"
        )

    if existing_hash != source_hash:
        raise ValueError(
            f"{spec.batch_id}: source contents changed for an existing batch ID"
        )


def _mark_attempt_started(
    connection: duckdb.DuckDBPyConnection,
    *,
    spec: BatchSpec,
    source_hash: str,
    existing_row,
) -> None:
    """
    Persist batch progress before landing rows.

    This small autocommitted status update survives a later landing transaction
    failure, so the failed attempt remains visible and can be retried.
    """
    if existing_row is None:
        connection.execute(
            """
            INSERT INTO ingestion_batches (
                batch_id,
                manifest_order,
                source_file,
                snapshot_date,
                source_sha256,
                status,
                first_started_at,
                last_attempt_at,
                landed_at,
                validated_at,
                completed_at,
                attempt_count,
                input_lines,
                technically_valid_lines,
                technically_invalid_lines,
                unique_event_lines,
                duplicate_event_lines,
                applied_state_changes,
                stale_event_lines,
                business_quality_issues,
                error_message
            )
            VALUES (
                ?, ?, ?, ?, ?,
                'processing',
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP,
                NULL,
                NULL,
                NULL,
                1,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                NULL
            )
            """,
            [
                spec.batch_id,
                spec.manifest_order,
                spec.relative_file,
                spec.snapshot_date,
                source_hash,
            ],
        )
        return

    connection.execute(
        """
        UPDATE ingestion_batches
        SET
            manifest_order = ?,
            status = 'processing',
            last_attempt_at = CURRENT_TIMESTAMP,
            attempt_count = attempt_count + 1,
            input_lines = 0,
            technically_valid_lines = 0,
            technically_invalid_lines = 0,
            unique_event_lines = 0,
            duplicate_event_lines = 0,
            applied_state_changes = 0,
            stale_event_lines = 0,
            business_quality_issues = 0,
            error_message = NULL
        WHERE batch_id = ?
        """,
        [spec.manifest_order, spec.batch_id],
    )


def _land_one_batch(
    connection: duckdb.DuckDBPyConnection,
    *,
    spec: BatchSpec,
    fail_after_lines: int | None = None,
) -> BatchLandingResult:
    source_hash = sha256_file(spec.source_path)
    existing_row = _get_existing_batch(connection, spec.batch_id)

    if existing_row is not None:
        _assert_batch_identity_is_stable(
            spec=spec,
            source_hash=source_hash,
            existing_row=existing_row,
        )

        existing_status = existing_row[4]
        existing_input_lines = int(existing_row[5])

        if existing_status in {"landed", "completed"}:
            return BatchLandingResult(
                batch_id=spec.batch_id,
                action="skipped",
                input_lines=existing_input_lines,
            )

    _mark_attempt_started(
        connection,
        spec=spec,
        source_hash=source_hash,
        existing_row=existing_row,
    )

    line_count = 0

    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            "DELETE FROM raw_visit_lines WHERE batch_id = ?",
            [spec.batch_id],
        )

        with spec.source_path.open("rb") as file:
            for line_number, raw_line in enumerate(file, start=1):
                line_count = line_number

                # Preserve the exact source line fingerprint including its
                # original line ending, but store payload text without CR/LF.
                line_hash = hashlib.sha256(raw_line).hexdigest()
                raw_payload = raw_line.rstrip(b"\r\n").decode("utf-8")

                connection.execute(
                    """
                    INSERT INTO raw_visit_lines (
                        batch_id,
                        line_number,
                        raw_payload,
                        line_sha256
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        spec.batch_id,
                        line_number,
                        raw_payload,
                        line_hash,
                    ],
                )

                if (
                    fail_after_lines is not None
                    and line_number >= fail_after_lines
                ):
                    raise RuntimeError(
                        f"Simulated landing failure for {spec.batch_id} "
                        f"after {line_number} lines"
                    )

        connection.execute(
            """
            UPDATE ingestion_batches
            SET
                status = 'landed',
                input_lines = ?,
                landed_at = CURRENT_TIMESTAMP,
                error_message = NULL
            WHERE batch_id = ?
            """,
            [line_count, spec.batch_id],
        )
        connection.execute("COMMIT")

        return BatchLandingResult(
            batch_id=spec.batch_id,
            action="landed",
            input_lines=line_count,
        )

    except Exception as exc:
        connection.execute("ROLLBACK")

        # Record the failure separately after rollback. No partial raw landing
        # rows from this failed attempt remain committed.
        connection.execute(
            """
            UPDATE ingestion_batches
            SET
                status = 'failed',
                input_lines = 0,
                landed_at = NULL,
                error_message = ?
            WHERE batch_id = ?
            """,
            [str(exc), spec.batch_id],
        )
        raise


def land_visit_batches(
    *,
    db_path: Path | str | None = None,
    fail_batch_id: str | None = None,
    fail_after_lines: int | None = None,
) -> list[BatchLandingResult]:
    """
    Land all visit-change batches in manifest order.

    Already landed/completed batches are strict no-ops. `fail_batch_id` and
    `fail_after_lines` exist only to test restart behaviour.
    """
    connection = connect_database(db_path=db_path)
    apply_schema(connection)

    try:
        results: list[BatchLandingResult] = []

        for spec in load_manifest():
            injected_failure = (
                fail_batch_id == spec.batch_id
                and fail_after_lines is not None
            )

            result = _land_one_batch(
                connection,
                spec=spec,
                fail_after_lines=(
                    fail_after_lines if injected_failure else None
                ),
            )
            results.append(result)

        return results

    finally:
        connection.close()


def main() -> None:
    results = land_visit_batches()

    connection = connect_database(read_only=True)
    try:
        total_lines = connection.execute(
            "SELECT COUNT(*) FROM raw_visit_lines"
        ).fetchone()[0]

        landed_batches = connection.execute(
            """
            SELECT COUNT(*)
            FROM ingestion_batches
            WHERE status IN ('landed', 'completed')
            """
        ).fetchone()[0]
    finally:
        connection.close()

    print("=" * 84)
    print("VISIT BATCH LANDING")
    print("=" * 84)

    for result in results:
        label = "SKIP" if result.action == "skipped" else "OK"
        print(
            f"[{label:<4}] {result.batch_id:<12} "
            f"action={result.action:<8} lines={result.input_lines}"
        )

    print("-" * 84)
    print(f"Database: {DB_PATH}")
    print(f"Manifest batches landed/completed: {landed_batches}")
    print(f"Raw visit input lines preserved:   {total_lines}")
    print(
        "SUCCESS: Visit batches are landed in manifest order and are safe to replay."
    )


if __name__ == "__main__":
    main()
