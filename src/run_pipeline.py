from __future__ import annotations

import argparse
from collections.abc import Callable

from src.build_analytical_facts import build_analytical_facts
from src.build_management_history import build_management_history
from src.build_reporting_marts import build_reporting_marts
from src.check_visit_quality import check_visit_quality
from src.database import DB_PATH, connect_database
from src.generate_report import generate_operations_report
from src.land_visit_batches import land_visit_batches
from src.load_reference_data import load_reference_data
from src.pipeline_presenter import (
    finish_stage,
    print_stage_intro,
    print_validation_summary,
    show_stage_2_results,
    show_stage_3_results,
    show_stage_4_results,
    show_stage_5_results,
    show_stage_6_results,
    show_stage_7_results,
    show_stage_8_results,
    show_stage_9_results,
    show_stage_10_results,
)
from src.process_visit_state import process_visit_state
from src.validate_visit_events import validate_visit_events
from src.verify_sources import verify_sources


TOTAL_STAGES = 10


def _remove_generated_database() -> None:
    for path in (
        DB_PATH,
        DB_PATH.with_name(DB_PATH.name + ".wal"),
    ):
        if path.exists():
            path.unlink()


def _with_connection(renderer: Callable) -> None:
    connection = connect_database(read_only=True)
    try:
        renderer(connection)
    finally:
        connection.close()


def run_pipeline(
    *,
    fresh: bool = False,
    pause: bool = True,
) -> None:
    """
    Run the complete core pipeline through the final Stage 10 report.

    By default the runner is interview-friendly: each stage explains its aim,
    questions, results and test coverage, then waits for Enter.

    Use --no-pause for CI or an unattended clean rebuild.
    """
    if fresh:
        print("Removing generated DuckDB state for a clean rebuild...")
        _remove_generated_database()

    # Stage 1
    print_stage_intro(1, TOTAL_STAGES)
    if not verify_sources():
        raise RuntimeError("Source integrity verification failed")
    print_validation_summary(1)
    finish_stage(1, TOTAL_STAGES, pause=pause)

    # Stage 2
    print_stage_intro(2, TOTAL_STAGES)
    load_reference_data()
    _with_connection(show_stage_2_results)
    print_validation_summary(2)
    finish_stage(2, TOTAL_STAGES, pause=pause)

    # Stage 3
    print_stage_intro(3, TOTAL_STAGES)
    land_visit_batches()
    _with_connection(show_stage_3_results)
    print_validation_summary(3)
    finish_stage(3, TOTAL_STAGES, pause=pause)

    # Stage 4
    print_stage_intro(4, TOTAL_STAGES)
    validate_visit_events()
    _with_connection(show_stage_4_results)
    print_validation_summary(4)
    finish_stage(4, TOTAL_STAGES, pause=pause)

    # Stage 5
    print_stage_intro(5, TOTAL_STAGES)
    process_visit_state()
    _with_connection(show_stage_5_results)
    print_validation_summary(5)
    finish_stage(5, TOTAL_STAGES, pause=pause)

    # Stage 6
    print_stage_intro(6, TOTAL_STAGES)
    check_visit_quality()
    _with_connection(show_stage_6_results)
    print_validation_summary(6)
    finish_stage(6, TOTAL_STAGES, pause=pause)

    # Stage 7
    print_stage_intro(7, TOTAL_STAGES)
    build_management_history()
    _with_connection(show_stage_7_results)
    print_validation_summary(7)
    finish_stage(7, TOTAL_STAGES, pause=pause)

    # Stage 8
    print_stage_intro(8, TOTAL_STAGES)
    build_analytical_facts()
    _with_connection(show_stage_8_results)
    print_validation_summary(8)
    finish_stage(8, TOTAL_STAGES, pause=pause)

    # Stage 9
    print_stage_intro(9, TOTAL_STAGES)
    build_reporting_marts()
    _with_connection(show_stage_9_results)
    print_validation_summary(9)
    finish_stage(9, TOTAL_STAGES, pause=pause)

    # Stage 10
    print_stage_intro(10, TOTAL_STAGES)
    report = generate_operations_report()
    show_stage_10_results(
        report_path=str(report.report_path),
        extract_paths=[str(path) for path in report.extract_paths],
        latest_batch_id=report.latest_batch_id,
    )
    print_validation_summary(10)
    finish_stage(10, TOTAL_STAGES, pause=pause)

    print()
    print("=" * 96)
    print("PIPELINE COMPLETE THROUGH STAGE 10")
    print("=" * 96)
    print(f"Database: {DB_PATH}")
    print("HTML report: reports/operations_report.html")
    print("Audit report: reports/Report.txt")
    print("Model extracts: reports/extracts/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the property-operations pipeline through Stage 10."
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete the generated DuckDB file before rebuilding everything.",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Do not wait for Enter between stages; useful for CI/unattended runs.",
    )
    args = parser.parse_args()

    run_pipeline(
        fresh=args.fresh,
        pause=not args.no_pause,
    )


if __name__ == "__main__":
    main()
