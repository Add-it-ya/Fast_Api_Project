#!/bin/sh
set -e

# Each uvicorn worker keeps its own metrics registry, so a scrape would land
# on whichever worker answered and counters would appear to jump between
# processes. prometheus_client aggregates across them through this directory;
# it must start empty or stale files from the last run are counted.
if [ -n "$PROMETHEUS_MULTIPROC_DIR" ]; then
    rm -rf "$PROMETHEUS_MULTIPROC_DIR"
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
    echo "Metrics aggregating through $PROMETHEUS_MULTIPROC_DIR"
fi

echo "Applying database migrations..."
alembic upgrade head

echo "Starting API..."
# --no-access-log: the LoggingMiddleware already emits one line per request,
# and uvicorn's own access log is a second synchronous write per request.
# WEB_CONCURRENCY: one event loop saturates a single core; keep
# WEB_CONCURRENCY * (DB_POOL_SIZE + DB_MAX_OVERFLOW) under postgres
# max_connections.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log \
    --workers "${WEB_CONCURRENCY:-1}"
