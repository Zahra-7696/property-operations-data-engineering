from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.audit_report import write_processing_report
from src.database import PROJECT_ROOT, apply_schema, connect_database


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports"
TEMPLATE_DIR = PROJECT_ROOT / "reports" / "templates"


@dataclass(frozen=True)
class ReportBuildResult:
    report_path: Path
    extract_paths: tuple[Path, ...]
    latest_batch_id: str


def _query_dicts(connection, sql: str) -> list[dict[str, object]]:
    cursor = connection.execute(sql)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        path.write_text("", encoding="utf-8")
        return

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_html_report(path: Path, html: str) -> None:
    """Write the HTML report safely, including on Windows.

    The report may already exist and may be open in a browser during an
    interview/demo. Writing to a temporary file first avoids truncating the
    existing report if the new write fails and also avoids the Windows
    ``OSError: [Errno 22] Invalid argument`` seen with a direct Path.write_text
    replacement on some local setups.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(html, encoding="utf-8")

    try:
        os.replace(temporary_path, path)
    except OSError as exc:
        # A stale/problematic existing destination can occasionally make a
        # direct Windows replacement fail. If possible, remove that destination
        # and replace it with the fully-written temporary report.
        try:
            if path.exists() and path.is_file():
                path.unlink()
            os.replace(temporary_path, path)
        except OSError as replacement_exc:
            raise RuntimeError(
                "Could not write reports/operations_report.html. "
                "Close any application that may have locked the file, confirm "
                "that reports/operations_report.html is a normal file (not a "
                "folder), then rerun Stage 10."
            ) from replacement_exc
    finally:
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _line_chart(
    company_week: list[dict[str, object]],
    *,
    metric: str,
    title: str,
    y_axis_title: str,
    include_plotlyjs: bool,
) -> str:
    figure = go.Figure()

    companies = sorted({str(row["company_name"]) for row in company_week})

    for company in companies:
        subset = [
            row
            for row in company_week
            if str(row["company_name"]) == company
        ]
        figure.add_trace(
            go.Scatter(
                x=[row["week_start"] for row in subset],
                y=[row[metric] for row in subset],
                mode="lines+markers",
                name=company,
            )
        )

    figure.update_layout(
        title=title,
        xaxis_title="Local week starting Monday",
        yaxis_title=y_axis_title,
        legend_title="Managing company",
        margin=dict(l=45, r=20, t=55, b=45),
        height=390,
        template="plotly_white",
    )

    return pio.to_html(
        figure,
        full_html=False,
        include_plotlyjs="inline" if include_plotlyjs else False,
        config={"displayModeBar": False, "responsive": True},
    )


def _contractor_chart(contractors: list[dict[str, object]]) -> str:
    ordered = sorted(
        contractors,
        key=lambda row: int(row["visit_count"]),
        reverse=True,
    )

    figure = go.Figure(
        go.Bar(
            x=[row["visit_count"] for row in ordered],
            y=[row["contractor_name"] for row in ordered],
            orientation="h",
        )
    )

    figure.update_layout(
        title="Visits by contractor",
        xaxis_title="Visit count",
        yaxis_title="",
        yaxis=dict(autorange="reversed"),
        margin=dict(l=110, r=20, t=55, b=45),
        height=410,
        template="plotly_white",
    )

    return pio.to_html(
        figure,
        full_html=False,
        include_plotlyjs=False,
        config={"displayModeBar": False, "responsive": True},
    )


def generate_operations_report(
    *,
    db_path: Path | str | None = None,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> ReportBuildResult:
    """Generate the self-contained HTML operations report and CSV extracts."""
    output_root = Path(output_dir)
    extract_dir = output_root / "extracts"
    report_path = output_root / "operations_report.html"

    connection = connect_database(db_path=db_path)
    apply_schema(connection)

    try:
        mart_count = connection.execute(
            "SELECT COUNT(*) FROM rpt_company_week"
        ).fetchone()[0]

        if mart_count == 0:
            raise RuntimeError(
                "Reporting marts are empty. "
                "Run: python -m src.build_reporting_marts"
            )

        first_week, last_week = connection.execute(
            """
            SELECT MIN(week_start), MAX(week_start)
            FROM src_report_weeks
            """
        ).fetchone()

        latest_batch = connection.execute(
            """
            SELECT batch_id
            FROM ingestion_batches
            WHERE status = 'completed'
            ORDER BY manifest_order DESC
            LIMIT 1
            """
        ).fetchone()

        if latest_batch is None:
            raise RuntimeError("No successfully completed visit batch was found")

        latest_batch_id = str(latest_batch[0])

        company_week = _query_dicts(
            connection,
            """
            SELECT *
            FROM rpt_company_week
            ORDER BY week_start, company_id
            """,
        )

        company_period = _query_dicts(
            connection,
            """
            SELECT *
            FROM rpt_company_period
            ORDER BY company_id
            """,
        )

        property_week = _query_dicts(
            connection,
            """
            SELECT *
            FROM rpt_property_week
            ORDER BY property_id, week_start
            """,
        )

        contractors = _query_dicts(
            connection,
            """
            SELECT *
            FROM rpt_contractor
            ORDER BY visit_count DESC, contractor_id
            """,
        )

        overall = connection.execute(
            """
            SELECT
                COUNT(*) AS visits,
                COUNT(DISTINCT visitor_id) AS visitors,
                ROUND(SUM(COALESCE(completed_hours, 0)), 2) AS hours,
                SUM(CASE WHEN is_unfinished THEN 1 ELSE 0 END) AS unfinished
            FROM fact_visits
            WHERE is_requested_week = TRUE
            """
        ).fetchone()

        incident_overall = connection.execute(
            """
            SELECT
                COUNT(*) AS incidents,
                SUM(CASE WHEN is_high THEN 1 ELSE 0 END) AS high_incidents
            FROM fact_incidents
            WHERE is_requested_week = TRUE
            """
        ).fetchone()

        quality = {
            "input_lines": connection.execute(
                "SELECT COUNT(*) FROM raw_visit_lines"
            ).fetchone()[0],
            "technical_rejects": connection.execute(
                "SELECT COUNT(*) FROM visit_event_rejects"
            ).fetchone()[0],
            "duplicate_deliveries": connection.execute(
                "SELECT COUNT(*) FROM visit_event_duplicates"
            ).fetchone()[0],
            "business_quality_visits": connection.execute(
                "SELECT COUNT(DISTINCT visit_id) FROM visit_quality_issues"
            ).fetchone()[0],
            "deleted_tombstones": connection.execute(
                "SELECT COUNT(*) FROM visit_current_state WHERE is_deleted = TRUE"
            ).fetchone()[0],
            "outside_week_visits": connection.execute(
                "SELECT COUNT(*) FROM fact_visits WHERE is_requested_week = FALSE"
            ).fetchone()[0],
            "outside_week_incidents": connection.execute(
                "SELECT COUNT(*) FROM fact_incidents WHERE is_requested_week = FALSE"
            ).fetchone()[0],
            "zero_property_weeks": connection.execute(
                "SELECT COUNT(*) FROM rpt_property_week WHERE has_activity = FALSE"
            ).fetchone()[0],
        }

        company_by_visits = max(
            company_period,
            key=lambda row: int(row["visit_count"]),
        )
        company_by_hours = max(
            company_period,
            key=lambda row: float(row["completed_visit_hours"]),
        )
        company_by_high = max(
            company_period,
            key=lambda row: int(row["high_severity_incidents"]),
        )

        top_contractor = max(
            contractors,
            key=lambda row: int(row["visit_count"]),
        )

        weekly_visits_chart = _line_chart(
            company_week,
            metric="visit_count",
            title="Weekly visits by managing company",
            y_axis_title="Visits",
            include_plotlyjs=True,
        )

        weekly_incidents_chart = _line_chart(
            company_week,
            metric="incident_count",
            title="Weekly incidents by managing company",
            y_axis_title="Incidents",
            include_plotlyjs=False,
        )

        contractor_chart = _contractor_chart(contractors)

        kpis = [
            {"label": "Visits", "value": f"{overall[0]:,}"},
            {"label": "Distinct visitors", "value": f"{overall[1]:,}"},
            {"label": "Completed hours", "value": f"{float(overall[2]):,.2f}"},
            {"label": "Unfinished visits", "value": f"{overall[3]:,}"},
            {"label": "Incidents", "value": f"{incident_overall[0]:,}"},
            {"label": "High severity", "value": f"{incident_overall[1]:,}"},
        ]

        quality_rows = [
            {
                "measure": "Physical visit-change input lines",
                "value": quality["input_lines"],
                "interpretation": "All source lines were preserved before parsing.",
            },
            {
                "measure": "Technical rejects",
                "value": quality["technical_rejects"],
                "interpretation": "Malformed/incomplete event lines retained with evidence.",
            },
            {
                "measure": "Duplicate event deliveries",
                "value": quality["duplicate_deliveries"],
                "interpretation": "Repeated event_id deliveries counted once logically.",
            },
            {
                "measure": "Business-invalid active visits",
                "value": quality["business_quality_visits"],
                "interpretation": "Well-formed visits excluded from metrics by business rules.",
            },
            {
                "measure": "Deleted visit tombstones",
                "value": quality["deleted_tombstones"],
                "interpretation": "Latest delete states retained to prevent stale resurrection.",
            },
            {
                "measure": "Valid visits outside requested weeks",
                "value": quality["outside_week_visits"],
                "interpretation": "Valid facts retained but outside this report period.",
            },
            {
                "measure": "Valid incidents outside requested weeks",
                "value": quality["outside_week_incidents"],
                "interpretation": "Valid facts retained but outside this report period.",
            },
            {
                "measure": "Zero-activity property/week rows",
                "value": quality["zero_property_weeks"],
                "interpretation": "Explicitly retained in the 24 x 6 property-week scaffold.",
            },
        ]

        definitions = [
            (
                "A reporting week starts Monday 00:00 in each property's IANA timezone; "
                "company charts use those property-local week labels."
            ),
            (
                "A visit is attributed to the company managing its property at started_at; "
                "an incident uses reported_at."
            ),
            (
                "An unfinished visit counts as a visit and distinct visitor but contributes "
                "no completed hours."
            ),
            (
                "Completed visit hours are elapsed UTC/TIMESTAMPTZ time for the whole visit, "
                "assigned to its start week and rounded only after aggregation."
            ),
            (
                "High severity means exactly severity = 'high'."
            ),
            (
                "Distinct visitors are recalculated within each displayed group and are "
                "not additive across companies, properties or weeks."
            ),
        ]

        findings = [
            (
                f"{company_by_visits['company_name']} has the highest visit count "
                f"({company_by_visits['visit_count']}) in the requested period."
            ),
            (
                f"{company_by_hours['company_name']} has the largest completed-visit "
                f"hours total ({float(company_by_hours['completed_visit_hours']):.2f})."
            ),
            (
                f"{company_by_high['company_name']} has the highest count of high-severity "
                f"incidents ({company_by_high['high_severity_incidents']}); this is a count, "
                "not a risk rate."
            ),
            (
                f"{top_contractor['contractor_name']} has the most requested-period visits "
                f"({top_contractor['visit_count']})."
            ),
            (
                f"{overall[3]} unfinished visits merit operational follow-up because the "
                "data cannot distinguish an active visit from a missing checkout."
            ),
            (
                f"{quality['zero_property_weeks']} property/week combinations have no visits "
                "or incidents and remain visible in the property summary."
            ),
        ]

        limitations = [
            (
                "Incidents are not linked to a specific visit or contractor, so the report "
                "does not attribute incidents to contractors."
            ),
            (
                "High-severity incident counts cannot by themselves establish comparative "
                "risk because the supplied data does not provide a complete exposure denominator."
            ),
            (
                "An unfinished visit may represent a visitor still on site or a missing "
                "checkout; the source cannot distinguish those explanations."
            ),
            (
                "The report uses the latest successfully loaded visit state, including later "
                "corrections, rather than reconstructing what an earlier published report knew."
            ),
        ]

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = env.get_template("operations_report.html.j2")

        output_root.mkdir(parents=True, exist_ok=True)

        html = template.render(
            title="Meadow Operations — Weekly Operations Report",
            first_week=first_week,
            last_week=last_week,
            latest_batch_id=latest_batch_id,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            kpis=kpis,
            weekly_visits_chart=weekly_visits_chart,
            weekly_incidents_chart=weekly_incidents_chart,
            contractor_chart=contractor_chart,
            company_period=company_period,
            property_week=property_week,
            contractors=contractors,
            zero_activity_property_weeks=quality["zero_property_weeks"],
            quality_rows=quality_rows,
            definitions=definitions,
            findings=findings,
            limitations=limitations,
        )

        _write_html_report(report_path, html)

        extract_data = {
            "company_week.csv": company_week,
            "company_period.csv": company_period,
            "property_week.csv": property_week,
            "contractor.csv": contractors,
            "data_quality_summary.csv": quality_rows,
        }

        extract_paths: list[Path] = []
        for filename, rows in extract_data.items():
            path = extract_dir / filename
            _write_csv(path, rows)
            extract_paths.append(path)

        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """
            DELETE FROM report_builds
            WHERE report_name = 'operations_report'
            """
        )
        connection.execute(
            """
            INSERT INTO report_builds (
                report_name,
                generated_at,
                report_path,
                extract_directory,
                latest_batch_id,
                first_report_week,
                last_report_week
            )
            VALUES (
                'operations_report',
                CURRENT_TIMESTAMP,
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,
            [
                str(report_path),
                str(extract_dir),
                latest_batch_id,
                first_week,
                last_week,
            ],
        )
        connection.execute("COMMIT")

        write_processing_report(connection)

        return ReportBuildResult(
            report_path=report_path,
            extract_paths=tuple(extract_paths),
            latest_batch_id=latest_batch_id,
        )

    except Exception:
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        raise

    finally:
        connection.close()


def main() -> None:
    result = generate_operations_report()

    print("=" * 96)
    print("OPERATIONS REPORT GENERATED")
    print("=" * 96)
    print(f"HTML report: {result.report_path}")
    print(f"Latest successfully processed batch: {result.latest_batch_id}")
    print("Model extracts:")
    for path in result.extract_paths:
        print(f"  - {path}")
    print()
    print(
        "SUCCESS: Viewable HTML report and modelled CSV extracts were generated."
    )


if __name__ == "__main__":
    main()
