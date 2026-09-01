# 0003. PostgreSQL over SQLite

**Status:** Accepted

## Context

The service persists user accounts and a prediction log. The prediction log
grows with every request and is read back through a filtered, sorted history
endpoint.

## Decision

PostgreSQL 16 over async SQLAlchemy and `asyncpg`, with Alembic migrations.

SQLite was the earlier choice and is genuinely good for a single-writer
workload. It was rejected here because:

- The service runs multiple workers writing concurrently. SQLite serialises
  writers and would make the prediction log a contention point.
- The history query needs a composite index with a descending sort column and
  benefits from `EXPLAIN (ANALYZE, BUFFERS)` to verify the plan. SQLite's
  planner and tooling are thinner.
- Bulk loading benchmark data uses `COPY`, which has no SQLite equivalent —
  measured at 48,000 rows/sec against 1,300 for row-by-row inserts.

## Consequences

An operational dependency that has to be run, healthchecked and connected to,
where SQLite is a file. `docker-compose` carries that weight locally.

Connection pools are sized per worker, so `WEB_CONCURRENCY x (DB_POOL_SIZE +
DB_MAX_OVERFLOW)` must stay under `max_connections`. Exceeding it surfaces as
`asyncpg.TooManyConnectionsError` under load rather than at startup — this was
hit for real while tuning worker count.

Schema changes go through Alembic rather than `create_all`, so the deployed
schema has a history and can be rolled back.
