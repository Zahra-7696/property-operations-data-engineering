from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.build_analytical_facts import build_analytical_facts
from src.build_management_history import build_management_history
from src.build_reporting_marts import build_reporting_marts
from src.check_visit_quality import check_visit_quality
from src.database import connect_database
from src.generate_report import generate_operations_report
from src.land_visit_batches import land_visit_batches
from src.load_reference_data import load_reference_data
from src.process_visit_state import process_visit_state
from src.validate_visit_events import validate_visit_events


@pytest.fixture(scope="module")
def report_build(tmp_path_factory):
    root = tmp_path_factory.mktemp("stage10")
    db_path = root / "stage10.duckdb"
    report_dir = root / "reports"

    load_reference_data(db_path=db_path)
    land_visit_batches(db_path=db_path)
    validate_visit_events(db_path=db_path)
    process_visit_state(db_path=db_path)
    check_visit_quality(db_path=db_path)
    build_management_history(db_path=db_path)
    build_analytical_facts(db_path=db_path)
    build_reporting_marts(db_path=db_path)

    result = generate_operations_report(
        db_path=db_path,
        output_dir=report_dir,
    )

    return {
        "db_path": db_path,
        "report_dir": report_dir,
        "result": result,
    }


def test_html_report_is_generated_and_viewable(report_build):
    path = report_build["result"].report_path

    assert path.exists()
    assert path.stat().st_size > 100_000

    html = path.read_text(encoding="utf-8")

    assert "<!doctype html>" in html.lower()
    assert "Meadow Operations — Weekly Operations Report" in html
    assert "Weekly activity by managing company" in html
    assert "Property summary" in html
    assert "Contractor visit activity" in html


def test_report_contains_required_kpis(report_build):
    html = report_build["result"].report_path.read_text(encoding="utf-8")

    assert "1,543" in html
    assert "259" in html
    assert "2,053.50" in html
    assert "91" in html
    assert "246" in html
    assert "31" in html


def test_report_contains_period_and_latest_batch(report_build):
    html = report_build["result"].report_path.read_text(encoding="utf-8")

    assert "2026-03-02" in html
    assert "2026-04-06" in html
    assert "batch_06" in html


def test_report_contains_quality_and_load_evidence(report_build):
    html = report_build["result"].report_path.read_text(encoding="utf-8")

    assert "1,997" in html
    assert "Technical rejects" in html
    assert ">18<" in html or ">18</td>" in html
    assert "Duplicate event deliveries" in html
    assert ">35<" in html or ">35</td>" in html
    assert "Business-invalid active visits" in html
    assert ">3<" in html or ">3</td>" in html
    assert "Zero-activity property/week rows" in html
    assert ">22<" in html or ">22</td>" in html


def test_model_extracts_are_generated_with_expected_rows(report_build):
    extract_dir = report_build["report_dir"] / "extracts"

    expected_counts = {
        "company_week.csv": 18,
        "company_period.csv": 3,
        "property_week.csv": 144,
        "contractor.csv": 8,
        "data_quality_summary.csv": 8,
    }

    for filename, expected in expected_counts.items():
        path = extract_dir / filename
        assert path.exists()

        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

        assert len(rows) == expected


def test_report_contains_findings_and_limitations(report_build):
    html = report_build["result"].report_path.read_text(encoding="utf-8")

    assert "Findings and operational follow-up" in html
    assert "What the data cannot establish" in html
    assert "not a risk rate" in html
    assert "does not attribute incidents to contractors" in html
    assert "missing checkout" in html


def test_report_build_metadata_is_recorded(report_build):
    connection = connect_database(
        read_only=True,
        db_path=report_build["db_path"],
    )

    try:
        row = connection.execute(
            """
            SELECT
                report_name,
                latest_batch_id,
                first_report_week,
                last_report_week
            FROM report_builds
            WHERE report_name = 'operations_report'
            """
        ).fetchone()
    finally:
        connection.close()

    assert row[0] == "operations_report"
    assert row[1] == "batch_06"
    assert str(row[2]) == "2026-03-02"
    assert str(row[3]) == "2026-04-06"


def test_report_regeneration_is_idempotent_at_artifact_grain(report_build):
    second = generate_operations_report(
        db_path=report_build["db_path"],
        output_dir=report_build["report_dir"],
    )

    connection = connect_database(
        read_only=True,
        db_path=report_build["db_path"],
    )

    try:
        build_rows = connection.execute(
            "SELECT COUNT(*) FROM report_builds"
        ).fetchone()[0]
    finally:
        connection.close()

    assert build_rows == 1
    assert second.report_path == report_build["result"].report_path
    assert len(second.extract_paths) == 5
