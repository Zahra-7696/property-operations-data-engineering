from __future__ import annotations

import argparse

from src.build_analytical_facts import build_analytical_facts
from src.build_management_history import build_management_history
from src.check_visit_quality import check_visit_quality
from src.database import DB_PATH
from src.land_visit_batches import land_visit_batches
from src.load_reference_data import load_reference_data
from src.process_visit_state import process_visit_state
from src.validate_visit_events import validate_visit_events
from src.verify_sources import verify_sources


def _remove_generated_database() -> None:
    for path in (
        DB_PATH,
        DB_PATH.with_name(DB_PATH.name + ".wal"),
    ):
        if path.exists():
            path.unlink()


def run_pipeline(*, fresh: bool = False) -> None:
    """
    Run the complete implemented pipeline through Stage 8.

    `fresh=True` removes the rebuildable DuckDB file first, which is the clearest
    way for a reviewer to reproduce the project from committed raw data and code.
    """
    if fresh:
        print("Removing generated DuckDB state for a clean rebuild...")
        _remove_generated_database()

    print("\n[1/8] Verify immutable source files")
    if not verify_sources():
        raise RuntimeError("Source integrity verification failed")

    print("\n[2/8] Load typed reference data")
    load_reference_data()

    print("\n[3/8] Land visit batches")
    land_visit_batches()

    print("\n[4/8] Technically validate visit events")
    validate_visit_events()

    print("\n[5/8] Deduplicate events and build current visit state")
    process_visit_state()

    print("\n[6/8] Apply current-visit business-quality checks")
    check_visit_quality()

    print("\n[7/8] Reconstruct property-management history")
    build_management_history()

    print("\n[8/8] Build analytical visit and incident facts")
    build_analytical_facts()

    print("\n" + "=" * 88)
    print("PIPELINE COMPLETE THROUGH STAGE 8")
    print("=" * 88)
    print(f"Database: {DB_PATH}")
    print("Audit report: reports/Report.txt")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the property-operations pipeline through Stage 8."
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete the generated DuckDB file before rebuilding everything.",
    )
    args = parser.parse_args()

    run_pipeline(fresh=args.fresh)


if __name__ == "__main__":
    main()
