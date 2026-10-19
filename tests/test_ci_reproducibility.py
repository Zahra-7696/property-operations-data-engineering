from __future__ import annotations

from pathlib import Path

from src.acceptance_check import (
    AUDIT_REPORT_PATH,
    DB_CHECKS,
    EXPECTED_EXTRACT_ROWS,
    REPORT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_ci_workflow_exists_and_has_required_triggers():
    text = _workflow_text()

    assert WORKFLOW_PATH.exists()
    assert "push:" in text
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert 'python-version: "3.11"' in text


def test_ci_runs_clean_rebuild_acceptance_and_full_tests():
    text = _workflow_text()

    assert "python -m src.verify_sources" in text
    assert "python -m src.run_pipeline --fresh --no-pause" in text
    assert "python -m src.acceptance_check" in text
    assert "python -m pytest -q" in text


def test_ci_protects_raw_inputs_and_uploads_report_artifacts():
    text = _workflow_text()

    assert "git diff --exit-code -- data/" in text
    assert "actions/upload-artifact@v4" in text
    assert "reports/operations_report.html" in text
    assert "reports/Report.txt" in text
    assert "reports/extracts/*.csv" in text


def test_acceptance_checks_cover_every_major_pipeline_layer():
    labels = {check.label for check in DB_CHECKS}

    expected_labels = {
        "Raw visit lines",
        "Technical rejects",
        "Unique visit events",
        "Current visit states",
        "Reportable current visits",
        "Management intervals",
        "Visit facts",
        "Incident facts",
        "Company/week mart rows",
        "Property/week mart rows",
        "Contractor mart rows",
        "Report build rows",
    }

    assert expected_labels.issubset(labels)


def test_acceptance_artifact_paths_and_expected_extract_counts_are_stable():
    assert REPORT_PATH == Path("reports/operations_report.html")
    assert AUDIT_REPORT_PATH == Path("reports/Report.txt")

    assert EXPECTED_EXTRACT_ROWS == {
        "reports/extracts/company_week.csv": 18,
        "reports/extracts/company_period.csv": 3,
        "reports/extracts/property_week.csv": 144,
        "reports/extracts/contractor.csv": 8,
        "reports/extracts/data_quality_summary.csv": 8,
    }
