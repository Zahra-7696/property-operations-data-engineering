from __future__ import annotations

import json
from pathlib import Path

import duckdb

from src.database import DATA_DIR, DB_PATH, PROJECT_ROOT, apply_schema, connect_database


REFERENCE_TABLES = {
    "data/companies.csv": "src_companies",
    "data/contractors.csv": "src_contractors",
    "data/properties.csv": "src_properties",
    "data/property_management.csv": "src_property_management",
    "data/incidents.csv": "src_incidents",
    "data/report_weeks.csv": "src_report_weeks",
}

DELETE_ORDER = [
    "src_incidents",
    "src_property_management",
    "src_report_weeks",
    "src_properties",
    "src_contractors",
    "src_companies",
]


def _duckdb_path(path: Path) -> str:
    """
    Return a safely quoted path literal for DuckDB SQL.

    as_posix() also makes Windows paths such as D:\\project\\... usable by DuckDB.
    """
    return path.resolve().as_posix().replace("'", "''")


def _expected_row_counts() -> dict[str, int]:
    manifest_path = PROJECT_ROOT / "DATASET_MANIFEST.json"

    with manifest_path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)

    return {
        relative_path: int(manifest[relative_path]["rows"])
        for relative_path in REFERENCE_TABLES
    }


def _load_companies(connection: duckdb.DuckDBPyConnection) -> None:
    path = _duckdb_path(DATA_DIR / "companies.csv")
    connection.execute(
        f"""
        INSERT INTO src_companies
        SELECT
            CAST(company_id AS VARCHAR),
            CAST(company_name AS VARCHAR)
        FROM read_csv_auto('{path}', header = true);
        """
    )


def _load_contractors(connection: duckdb.DuckDBPyConnection) -> None:
    path = _duckdb_path(DATA_DIR / "contractors.csv")
    connection.execute(
        f"""
        INSERT INTO src_contractors
        SELECT
            CAST(contractor_id AS VARCHAR),
            CAST(contractor_name AS VARCHAR)
        FROM read_csv_auto('{path}', header = true);
        """
    )


def _load_properties(connection: duckdb.DuckDBPyConnection) -> None:
    path = _duckdb_path(DATA_DIR / "properties.csv")
    connection.execute(
        f"""
        INSERT INTO src_properties
        SELECT
            CAST(property_id AS VARCHAR),
            CAST(property_name AS VARCHAR),
            CAST(timezone AS VARCHAR)
        FROM read_csv_auto('{path}', header = true);
        """
    )


def _load_property_management(connection: duckdb.DuckDBPyConnection) -> None:
    path = _duckdb_path(DATA_DIR / "property_management.csv")
    connection.execute(
        f"""
        INSERT INTO src_property_management
        SELECT
            CAST(observation_id AS VARCHAR),
            CAST(property_id AS VARCHAR),
            CAST(company_id AS VARCHAR),
            CAST(effective_at AS TIMESTAMPTZ)
        FROM read_csv_auto('{path}', header = true);
        """
    )


def _load_incidents(connection: duckdb.DuckDBPyConnection) -> None:
    path = _duckdb_path(DATA_DIR / "incidents.csv")
    connection.execute(
        f"""
        INSERT INTO src_incidents
        SELECT
            CAST(incident_id AS VARCHAR),
            CAST(property_id AS VARCHAR),
            CAST(reported_at AS TIMESTAMPTZ),
            CAST(severity AS VARCHAR)
        FROM read_csv_auto('{path}', header = true);
        """
    )


def _load_report_weeks(connection: duckdb.DuckDBPyConnection) -> None:
    path = _duckdb_path(DATA_DIR / "report_weeks.csv")
    connection.execute(
        f"""
        INSERT INTO src_report_weeks
        SELECT CAST(week_start AS DATE)
        FROM read_csv_auto('{path}', header = true);
        """
    )


def _validate_loaded_reference_data(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, int]:
    """
    Validate source counts and logical relationships before committing.

    Any failure raises an exception, which causes the whole reference load
    transaction to roll back.
    """
    expected_counts = _expected_row_counts()
    actual_counts: dict[str, int] = {}

    for relative_path, table_name in REFERENCE_TABLES.items():
        actual = connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]

        expected = expected_counts[relative_path]
        actual_counts[table_name] = actual

        if actual != expected:
            raise ValueError(
                f"{table_name}: expected {expected} rows but loaded {actual}"
            )

    invalid_management_properties = connection.execute(
        """
        SELECT COUNT(*)
        FROM src_property_management AS m
        LEFT JOIN src_properties AS p
            ON p.property_id = m.property_id
        WHERE p.property_id IS NULL
        """
    ).fetchone()[0]

    invalid_management_companies = connection.execute(
        """
        SELECT COUNT(*)
        FROM src_property_management AS m
        LEFT JOIN src_companies AS c
            ON c.company_id = m.company_id
        WHERE c.company_id IS NULL
        """
    ).fetchone()[0]

    invalid_incident_properties = connection.execute(
        """
        SELECT COUNT(*)
        FROM src_incidents AS i
        LEFT JOIN src_properties AS p
            ON p.property_id = i.property_id
        WHERE p.property_id IS NULL
        """
    ).fetchone()[0]

    invalid_severities = connection.execute(
        """
        SELECT COUNT(*)
        FROM src_incidents
        WHERE severity NOT IN ('low', 'medium', 'high')
        """
    ).fetchone()[0]

    if invalid_management_properties:
        raise ValueError(
            "Property-management observations contain unknown property IDs"
        )

    if invalid_management_companies:
        raise ValueError(
            "Property-management observations contain unknown company IDs"
        )

    if invalid_incident_properties:
        raise ValueError("Incidents contain unknown property IDs")

    if invalid_severities:
        raise ValueError("Incidents contain an unexpected severity value")

    return actual_counts


def load_reference_data(
    *,
    db_path: Path | str | None = None,
) -> dict[str, int]:
    """
    Rebuild the typed reference/source tables atomically.

    The raw CSV files remain unchanged. If loading or validation fails, DuckDB
    rolls back the whole transaction so the database is not left half-loaded.
    """
    connection = connect_database(db_path=db_path)
    apply_schema(connection)

    try:
        connection.execute("BEGIN TRANSACTION")

        for table_name in DELETE_ORDER:
            connection.execute(f"DELETE FROM {table_name}")

        _load_companies(connection)
        _load_contractors(connection)
        _load_properties(connection)
        _load_property_management(connection)
        _load_incidents(connection)
        _load_report_weeks(connection)

        counts = _validate_loaded_reference_data(connection)

        connection.execute("COMMIT")
        return counts

    except Exception:
        connection.execute("ROLLBACK")
        raise

    finally:
        connection.close()


def main() -> None:
    counts = load_reference_data()

    print("=" * 78)
    print("REFERENCE DATA LOAD COMPLETE")
    print("=" * 78)

    for table_name, row_count in counts.items():
        print(f"[OK]   {table_name:<35} rows={row_count}")

    print("-" * 78)
    print("Logical reference checks: PASS")
    print(f"Database: {DB_PATH}")
    print("SUCCESS: Reference data loaded and validated atomically.")


if __name__ == "__main__":
    main()
