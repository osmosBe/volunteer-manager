#!/bin/sh
set -eu

# Schema migration and optional demo seeding are explicit deployment/maintenance
# operations. The web container has one deterministic responsibility: serve the app.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
