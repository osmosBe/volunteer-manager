"""Safe database diagnostics shared by admin and debug endpoints."""

from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.config.settings import get_settings
from app.database.session import database_backend, get_engine

REQUIRED_TABLES = ("events", "volunteers", "shifts")


def expected_alembic_revision() -> str | None:
    """Return repository migration heads without opening a database connection."""
    config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    heads = ScriptDirectory.from_config(Config(str(config_path))).get_heads()
    return ",".join(sorted(heads)) or None


def _base_result(database_type: str) -> dict[str, Any]:
    return {
        "database_type": database_type,
        "database_reachable": False,
        "schema_initialized": False,
        "current_revision": None,
        "expected_revision": expected_alembic_revision(),
        "migration_pending": None,
        "tables": {name: False for name in REQUIRED_TABLES},
        "diagnostic_error": None,
    }


def inspect_database(engine: Engine) -> dict[str, Any]:
    """Inspect connectivity and schema state without exposing exception details."""
    result = _base_result(database_backend(engine.url))
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            result["database_reachable"] = True
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            result["tables"] = {name: name in table_names for name in REQUIRED_TABLES}
            has_version_table = "alembic_version" in table_names
            result["schema_initialized"] = has_version_table and all(
                result["tables"].values()
            )
            if has_version_table:
                revisions = sorted(
                    str(row[0])
                    for row in connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    )
                )
                result["current_revision"] = ",".join(revisions) or None
            result["migration_pending"] = (
                result["current_revision"] != result["expected_revision"]
            )
    except Exception:  # Diagnostic endpoints must also survive driver/OS failures.
        result["diagnostic_error"] = (
            "schema_inspection_failed"
            if result["database_reachable"]
            else "database_unreachable"
        )
    return result


def safe_database_diagnostics() -> dict[str, Any]:
    """Build diagnostics even when URL parsing or engine construction fails."""
    database_url = get_settings().database_url
    try:
        database_type = database_backend(database_url)
    except Exception:
        database_type = "unknown"
    try:
        engine = get_engine()
    except Exception:
        result = _base_result(database_type)
        result["diagnostic_error"] = "engine_initialization_failed"
        return result
    return inspect_database(engine)
