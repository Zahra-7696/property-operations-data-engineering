from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "DATASET_MANIFEST.json"


def sha256_file(path: Path) -> str:
    """Return the SHA256 checksum for a file."""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def count_csv_rows(path: Path) -> int:
    """Count CSV data rows, excluding the header row."""
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.reader(file)
        next(reader, None)  # Skip header.
        return sum(1 for _ in reader)


def count_jsonl_rows(path: Path) -> int:
    """
    Count physical JSONL input lines.

    We deliberately do not parse JSON here because malformed source lines are
    intentionally present in the supplied assignment data. Parsing and
    validation belong to the ingestion layer, not the integrity check.
    """
    with path.open("r", encoding="utf-8") as file:
        return sum(1 for _ in file)


def count_rows(path: Path) -> int:
    """Count rows according to the supported source-file type."""
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return count_csv_rows(path)

    if suffix == ".jsonl":
        return count_jsonl_rows(path)

    raise ValueError(f"Unsupported source file type: {path}")


def verify_sources() -> bool:
    """Verify supplied source row counts and SHA256 checksums."""
    if not MANIFEST_PATH.exists():
        print(f"[FAIL] Missing manifest: {MANIFEST_PATH}")
        return False

    with MANIFEST_PATH.open("r", encoding="utf-8") as file:
        expected_files: dict[str, dict[str, object]] = json.load(file)

    all_valid = True

    print("=" * 88)
    print("SOURCE DATA INTEGRITY CHECK")
    print("=" * 88)

    for relative_path, expected in expected_files.items():
        path = PROJECT_ROOT / relative_path

        if not path.exists():
            print(f"[FAIL] {relative_path}: file is missing")
            all_valid = False
            continue

        actual_rows = count_rows(path)
        actual_hash = sha256_file(path)

        expected_rows = int(expected["rows"])
        expected_hash = str(expected["sha256"])

        rows_ok = actual_rows == expected_rows
        hash_ok = actual_hash == expected_hash

        if rows_ok and hash_ok:
            print(f"[OK]   {relative_path:<52} rows={actual_rows}")
            continue

        print(f"[FAIL] {relative_path}")

        if not rows_ok:
            print(
                f"       row count mismatch: "
                f"expected={expected_rows}, actual={actual_rows}"
            )

        if not hash_ok:
            print(
                f"       SHA256 mismatch:\n"
                f"       expected={expected_hash}\n"
                f"       actual  ={actual_hash}"
            )

        all_valid = False

    print("-" * 88)

    if all_valid:
        print("SUCCESS: All supplied source files match DATASET_MANIFEST.json.")
    else:
        print("FAILURE: One or more supplied source files do not match the manifest.")

    return all_valid


if __name__ == "__main__":
    raise SystemExit(0 if verify_sources() else 1)
