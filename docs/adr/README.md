# Architecture decision records

Short records of the decisions that shaped this service, written when the
decision was made and left alone afterwards. Each one states what was actually
measured, and what it cost — a decision with no downside listed is usually a
decision nobody examined.

| # | Decision | Status |
|---|---|---|
| [0001](0001-offload-inference-to-a-thread.md) | Offload inference with `asyncio.to_thread` | Accepted |
| [0002](0002-redis-for-prediction-cache.md) | Cache predictions in Redis, not in process | Accepted |
| [0003](0003-postgresql-over-sqlite.md) | PostgreSQL over SQLite | Accepted |
| [0004](0004-batch-the-prediction-log.md) | Batch prediction-log writes off the request path | Accepted |
| [0005](0005-composite-index-column-order.md) | Index `(company, year, created_at DESC)` in that order | Accepted |
| [0006](0006-token-only-principal.md) | Authenticate `/predict` from token claims, no database lookup | Accepted |
| [0007](0007-psi-for-drift-detection.md) | Population Stability Index for input drift | Accepted |
