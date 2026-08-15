#!/bin/sh
set -eu

# One replica uses the persistent /data volume. Upgrade before serving traffic
# so this revision never queries an older SQLite schema. Alembic is idempotent.
alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
