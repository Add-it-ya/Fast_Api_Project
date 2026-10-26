# Cache hit vs miss

Four runs of `scripts/load_test.py` (1,000 requests each, two at 1 client and
two at 10) against commit `16cfaef`: model v4 (50 trees, depth 12) served with
`n_jobs=1`, 4 uvicorn workers, PostgreSQL and Redis on the same Docker Desktop
host as the load generator.

Each figure is the average of the two runs at that level.

| clients | hit p50 | miss p50 | hit p95 | miss p95 |
|---:|---:|---:|---:|---:|
| 1 | 3.7 ms | 10.1 ms | 6.0 ms | 12.9 ms |
| 10 | 14.9 ms | 36.5 ms | 30.1 ms | 84.1 ms |

The hit ratio of about 80% is set by the load test's `UNIQUE_VECTORS=200`,
which replays 200 feature vectors 1,000 times. It says nothing about real
traffic, where an exact repeat of all twelve features (kilometres included) is
much rarer.
