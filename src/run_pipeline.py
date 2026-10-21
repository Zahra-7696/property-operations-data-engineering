from __future__ import annotations

import argparse
import os
import webbrowser
from pathlib import Path

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
    show_stage_10_results,
    show_detailed_stage_evidence,
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


def _with_connection_and_evidence(
    stage_number: int,
) -> None:
    """Render one complete result/evidence view for the selected stage.

    The detailed evidence is generated from the same source as Report.txt, so
    the terminal pipeline does not print a compact table and then repeat the
    same information again.
    """
    connection = connect_database(read_only=True)
    try:
        show_detailed_stage_evidence(connection, stage_number)
    finally:
        connection.close()


def _open_html_report(report_path: Path | str) -> None:
    """Open the generated HTML report for local runs.

    GitHub Actions/CI uses ``--no-pause`` as well, so browser launch is skipped
    there. A local interactive run and a local unattended run both open the
    report automatically at the end.
    """
    path = Path(report_path).resolve()

    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        print("HTML report auto-open skipped in CI/headless environment.")
        return

    if not path.exists():
        print(f"HTML report could not be opened because it does not exist: {path}")
        return

    try:
        opened = webbrowser.open_new_tab(path.as_uri())
    except Exception as exc:
        opened = False
        print(f"Automatic browser open failed: {exc}")

    if opened:
        print(f"Opened HTML report in the default browser: {path}")
        return

    # Windows fallback: open with the application's normal file association.
    if os.name == "nt":
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
            print(f"Opened HTML report with the default application: {path}")
            return
        except OSError as exc:
            print(f"Automatic HTML report open failed: {exc}")

    print(f"Open the report manually: {path}")


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
    _with_connection_and_evidence(2)
    print_validation_summary(2)
    finish_stage(2, TOTAL_STAGES, pause=pause)

    # Stage 3
    print_stage_intro(3, TOTAL_STAGES)
    land_visit_batches()
    _with_connection_and_evidence(3)
    print_validation_summary(3)
    finish_stage(3, TOTAL_STAGES, pause=pause)

    # Stage 4
    print_stage_intro(4, TOTAL_STAGES)
    validate_visit_events()
    _with_connection_and_evidence(4)
    print_validation_summary(4)
    finish_stage(4, TOTAL_STAGES, pause=pause)

    # Stage 5
    print_stage_intro(5, TOTAL_STAGES)
    process_visit_state()
    _with_connection_and_evidence(5)
    print_validation_summary(5)
    finish_stage(5, TOTAL_STAGES, pause=pause)

    # Stage 6
    print_stage_intro(6, TOTAL_STAGES)
    check_visit_quality()
    _with_connection_and_evidence(6)
    print_validation_summary(6)
    finish_stage(6, TOTAL_STAGES, pause=pause)

    # Stage 7
    print_stage_intro(7, TOTAL_STAGES)
    build_management_history()
    _with_connection_and_evidence(7)
    print_validation_summary(7)
    finish_stage(7, TOTAL_STAGES, pause=pause)

    # Stage 8
    print_stage_intro(8, TOTAL_STAGES)
    build_analytical_facts()
    _with_connection_and_evidence(8)
    print_validation_summary(8)
    finish_stage(8, TOTAL_STAGES, pause=pause)

    # Stage 9
    print_stage_intro(9, TOTAL_STAGES)
    build_reporting_marts()
    _with_connection_and_evidence(9)
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
    print()

    # Open the final report automatically for both:
    #   python -m src.run_pipeline --fresh
    #   python -m src.run_pipeline --fresh --no-pause
    # Browser launch is skipped automatically in CI/GitHub Actions.
    _open_html_report(report.report_path)


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
