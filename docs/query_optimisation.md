# Optimising the prediction history query

`GET /predictions/history` returns the caller's own predictions, newest first:

```sql
SELECT * FROM predictions
WHERE user_id = 1
ORDER BY created_at DESC
LIMIT 50;
```

`company` and `year` can narrow it further, but this is the shape of every call.

Measured on PostgreSQL 16.15 against 1,000,000 rows (about 182 MB of heap),
seeded by `scripts/seed_predictions.py`. How much an index helps depends on how
many rows one user has, so it was measured twice on the same row count:

- **heavy accounts:** `--users 5`, about 200,000 predictions each, the kind of
  volume an integration calling the API all day would build up;
- **typical accounts:** `--users 1000`, about 1,000 predictions each.

Each figure is the median of six runs after one warm-up. Full plans and every
run time are in [explain_before.txt](explain_before.txt) and
[explain_after.txt](explain_after.txt).

## Before: single-column index, then a sort

The previous schema (revision `0004`) had an index on `user_id` alone. It finds
the user's rows, but in no particular order, so Postgres fetches every one of
them and sorts to find the newest 50.

For a heavy account that means 199,472 rows from about 8,000 heap pages, spread
across two parallel workers, then a top-N heapsort:

```
Limit  (actual time=59.457..63.697 rows=50 loops=1)
  Buffers: shared hit=35 read=23355
  ->  Gather Merge
        ->  Sort  (rows=39 loops=3)
              Sort Key: created_at DESC
              Sort Method: top-N heapsort  Memory: 48kB
              ->  Parallel Bitmap Heap Scan on predictions  (rows=66491 loops=3)
                    Recheck Cond: (user_id = 1)
                    ->  Bitmap Index Scan on ix_predictions_user_id  (rows=199472 loops=1)
Execution Time: 63.819 ms
```

For a typical account it is the same plan on about 1,000 rows: 991 buffers and
2.0 ms.

## The index

```sql
CREATE INDEX ix_predictions_user_id_created_at
    ON predictions (user_id, created_at DESC);
```

Applied by Alembic revision `0005`.

The equality column comes first, so a b-tree descent lands on the start of
that user's rows. Inside that range the entries are stored by
`created_at DESC`, which is the order the query asks for. So one index serves
the `WHERE` and the `ORDER BY`, and `LIMIT 50` stops after reading 50 entries.

The other order, `(created_at, user_id)`, would not work: the leading column has
no filter, so the scan could not narrow to one user before reading.

`DESC` is declared to match the query. Postgres can also walk an ascending index
backwards, so it is not strictly needed for this query.

## After: index scan, no sort

```
Limit  (actual time=0.023..0.133 rows=50 loops=1)
  Buffers: shared hit=53
  ->  Index Scan using ix_predictions_user_id_created_at on predictions
        Index Cond: (user_id = 1)
Execution Time: 0.209 ms
```

The plan is the same for both kinds of account: 53 buffers, whatever the user's
total.

## Result

| | heavy accounts, before | after | typical accounts, before | after |
|---|---:|---:|---:|---:|
| Median execution time | 62.8 ms | **0.196 ms** | 2.03 ms | **0.163 ms** |
| Buffers | 23,390 | **53** | 991 | **53** |
| Rows read to return 50 | 199,472 | 50 | 1,007 | 50 |
| Sort node | top-N heapsort | none | top-N heapsort | none |

About **320x** faster for a heavy account and **12x** for a typical one. For an
ordinary user the single-column index was already fast enough. The composite index matters because the cost before grew
with the user's row count and now does not. A heavy account would otherwise get
slower with every prediction it makes.

## What it costs

The new index is 38 MB on this table and is maintained on every insert. It
replaces two indexes: the single-column `user_id` index (about 7 MB), which it
covers, and the old `(company, year, created_at DESC)` index (38 MB) that
served the previous version of this endpoint. So the table carries less index
than before. The inserts are already off the request path and batched, see
`PredictionWriter`.

The `public_id` unique index from revision `0004` is another 37 to 39 MB. That
is the cost of returning an id from `/predict`. It is not part of this query.

## Reproducing

```bash
docker compose up -d postgres
alembic upgrade head
python scripts/seed_predictions.py --rows 1000000 --users 5 --truncate
docker exec postgres1 psql -U carprice -d carprice -c "VACUUM ANALYZE predictions"
docker exec postgres1 psql -U carprice -d carprice -c \
  "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM predictions WHERE user_id = 1 ORDER BY created_at DESC LIMIT 50"
```

For the before plan, run `alembic downgrade 0004`, which only puts the previous
indexes back, then `VACUUM ANALYZE` and repeat. `alembic upgrade head` restores
the new index. Run the query a few times and take the median; one warm run is
not a measurement.

## Loading the benchmark data

`scripts/seed_predictions.py` loads via PostgreSQL `COPY`
(`asyncpg.copy_records_to_table`) instead of row-by-row `INSERT`. Same hardware,
same run:

| method | rows/sec |
|---|---:|
| row-by-row `INSERT` | 2,248 |
| `COPY` (copy time only) | 33,463 |
| `COPY` end to end, incl. generating the rows in Python | 28,422 |

**15x faster.** Raw output in
[benchmarks/seed/copy_vs_insert.txt](../benchmarks/seed/copy_vs_insert.txt), loading 1,000,000
rows into the table with its four current indexes. Generating the synthetic tuples in Python is a real share of the
wall time and says nothing about the database, so the script times it separately
rather than folding it into the COPY figure.
