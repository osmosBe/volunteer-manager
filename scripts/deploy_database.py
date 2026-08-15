#!/usr/bin/env python3
"""Run controlled deployment-time database operations.

This script is the fixed entrypoint of the Azure Container Apps migration job.
It deliberately stays outside the web application startup lifecycle.
"""

import subprocess
import sys
from pathlib import Path

# Direct execution keeps Azure Job configuration free of fragile command arguments.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.services.seed import ensure_demo_data  # noqa: E402


def run_alembic() -> None:
    """Upgrade and report the schema revision without invoking a shell."""
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    subprocess.run(["alembic", "current"], check=True)


def seed_demo_data() -> None:
    """Create the explicitly enabled, idempotent fictional DEV dataset."""
    with get_session_factory()() as db:
        event = ensure_demo_data(db)
        print(f"Fictional demo data available for event: {event.slug}")


def main() -> None:
    run_alembic()
    if get_settings().seed_demo_data:
        seed_demo_data()
    else:
        print("Fictional demo seed disabled; schema migration only.")


if __name__ == "__main__":
    main()
