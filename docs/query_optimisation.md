# Optimising the prediction history query

`GET /predictions/history` answers "the most recent predictions for a given
company and model year":

```sql
SELECT id, company, year, fuel, transmission, km_driven,
       predicted_price, cache_hit, created_at
FROM predictions
WHERE company = 'Maruti' AND year = 2015
ORDER BY created_at DESC
LIMIT 50;
```

Measured on PostgreSQL 16.15 against 1,010,000 rows (187 MB heap before the
index), seeded by `scripts/seed_predictions.py`. 1,157 rows match the filter, so
the query returns roughly 0.1% of the table. Full plans are in
[explain_before.txt](explain_before.txt) and [explain_after.txt](explain_after.txt).

## Before: parallel sequential scan

```
Limit  (actual time=24.022..26.067 rows=50 loops=1)
  Buffers: shared hit=14538 read=5938
  ->  Gather Merge  (actual time=24.021..26.063 rows=50 loops=1)
        Workers Planned: 2  Workers Launched: 2
        ->  Sort  (actual time=21.829..21.833 rows=41 loops=3)
              Sort Key: created_at DESC
              Sort Method: top-N heapsort  Memory: 35kB
              ->  Parallel Seq Scan on predictions  (actual time=0.145..21.602 rows=386 loops=3)
                    Filter: (((company)::text = 'Maruti'::text) AND (year = 2015))
                    Rows Removed by Filter: 336281
Execution Time: 26.116 ms
```

With only a primary key on `id`, nothing helps either half of the query. Postgres
reads the entire table across three parallel workers, and each one throws away
**336,281 rows** to find its share of the 1,157 matches. It then has to sort what
survives, because `created_at DESC` has no ordering to inherit. 20,476 buffers
are touched to return 50 rows.

Repeated runs: 26.1, 28.7, 29.8, 31.6, 33.0 ms.

## The index

```sql
CREATE INDEX ix_predictions_company_year_created_at
    ON predictions (company, year, created_at DESC);
```

Applied by Alembic revision `0002`.

**Column order is the whole point.** The two equality predicates come first, so a
b-tree descent lands directly on the `('Maruti', 2015)` range. Within that range
entries are already stored by `created_at DESC` — the exact order the query asks
for. One index therefore serves the `WHERE` *and* the `ORDER BY`.

The `DESC` matters less than it looks: Postgres can walk a b-tree backwards, so
an ascending index would also avoid the sort here. Declaring the direction the
query uses keeps the intent obvious and matters as soon as a second sort column
with a different direction appears.

Ordering the columns the other way round — `(created_at, company, year)` — would
be close to useless. The leading column would carry no filter, so the scan could
not narrow to the matching rows before reading them.

## After: index scan, no sort

```
Limit  (actual time=0.025..0.154 rows=50 loops=1)
  Buffers: shared hit=53
  ->  Index Scan using ix_predictions_company_year_created_at on predictions
        (actual time=0.024..0.150 rows=50 loops=1)
        Index Cond: (((company)::text = 'Maruti'::text) AND (year = 2015))
        Buffers: shared hit=53
Execution Time: 0.177 ms
```

Repeated runs: 0.175, 0.177, 0.178, 0.182, 0.196, 0.276 ms.

## Result

| | before | after | change |
|---|---:|---:|---|
| Execution time | 26.1 ms | 0.177 ms | **147x faster** |
| Buffers touched | 20,476 | 53 | **386x fewer** |
| Rows discarded by filter | 336,281 per worker | 0 | — |
| Parallel workers | 2 | 0 | not needed |
| Sort node | top-N heapsort | none | eliminated |

The sort disappearing is the more interesting half. The scan going from
sequential to indexed is what most people expect from an index; having the same
index also deliver the rows pre-ordered is what removes the `Sort` node and lets
`LIMIT 50` stop after 50 rows instead of after sorting every match.

## What it costs

The index is 39 MB against a 187 MB table, and it has to be maintained on every
insert. That is a real cost on a write-heavy table, and this one is written to on
every prediction — see `PredictionWriter`, which batches those inserts. It is
worth it here because the read is user-facing and the write is already
asynchronous and batched.

## Reproducing

```bash
docker-compose up -d
python scripts/seed_predictions.py --rows 1000000 --truncate --compare
docker exec postgres1 psql -U carprice -d carprice \
  -c "EXPLAIN (ANALYZE, BUFFERS) SELECT ... ;"
```

To see the slow plan again, drop the index (`alembic downgrade 0001`), run
`ANALYZE predictions`, and repeat. Take a representative run rather than the
first: a single warm execution is not a measurement.

## Loading the benchmark data

`scripts/seed_predictions.py` loads via PostgreSQL `COPY`
(`asyncpg.copy_records_to_table`) instead of row-by-row `INSERT`. Same hardware,
same run:

| method | rows/sec |
|---|---:|
| row-by-row `INSERT` | 1,346 |
| `COPY` (copy time only) | 48,040 |
| `COPY` end to end, incl. generating the rows in Python | 38,142 |

**36x faster.** Generating the synthetic tuples in Python is a real share of the
wall time and says nothing about the database, so the script times it separately
rather than folding it into the COPY figure.
