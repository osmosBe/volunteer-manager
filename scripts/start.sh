#!/bin/sh
set -eu

# During a revision change, Azure can briefly overlap an old and a new replica.
# SQLite permits only one schema writer, so retry only its transient lock error.
# All other migration errors fail the revision immediately and remain visible.
attempt=1
max_attempts=12
while :; do
    if migration_output="$(alembic upgrade head 2>&1)"; then
        printf '%s\n' "$migration_output"
        break
    fi

    printf '%s\n' "$migration_output" >&2
    if ! printf '%s' "$migration_output" | grep -q "database is locked" \
        || [ "$attempt" -ge "$max_attempts" ]; then
        exit 1
    fi

    printf 'SQLite migration is locked; retrying (%s/%s).\n' "$attempt" "$max_attempts" >&2
    sleep 5
    attempt=$((attempt + 1))
done

case "${SEED_DEMO_DATA:-false}" in
    true|TRUE|1|yes|YES)
        python -m scripts.seed_default_event
        ;;
esac

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
