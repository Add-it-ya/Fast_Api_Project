#!/bin/sh
set -e

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
