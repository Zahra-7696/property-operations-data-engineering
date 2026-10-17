from __future__ import annotations

from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
MODELLED_DIR = DATA_DIR / "modelled"
DB_PATH = MODELLED_DIR / "onside.duckdb"
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"


def connect_database(*, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the project DuckDB database."""
    MODELLED_DIR.mkdir(parents=True, exist_ok=True)

    if read_only and not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database does not exist yet: {DB_PATH}\n"
            "Run: python -m src.load_reference_data"
        )

    return duckdb.connect(str(DB_PATH), read_only=read_only)


def apply_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create project tables defined in sql/schema.sql."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    connection.execute(schema_sql)
