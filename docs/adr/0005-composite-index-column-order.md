# 0005. Index (user_id, created_at DESC) in that order

**Status:** Accepted. Replaces the earlier `(company, year, created_at DESC)`
index, which served a version of history that searched every user's
predictions.

## Context

`GET /predictions/history` returns the caller's own predictions, newest first.
With an index on `user_id` alone, Postgres fetches all of the user's rows and
sorts them to find the newest 50. On 1,000,000 rows that took 62.8 ms for an
account with 200,000 predictions and 2.0 ms for one with 1,000.

## Decision

One composite index, equality column first, sort column last, in the direction
the query asks for:

```sql
CREATE INDEX ix_predictions_user_id_created_at
    ON predictions (user_id, created_at DESC);
```

A b-tree descent lands on the start of the user's rows, which are already in
`created_at DESC` order, so `LIMIT 50` stops after 50 entries and the sort step
goes away. `(created_at, user_id)` would not work, because the leading column
carries no filter.

## Consequences

0.196 ms for the heavy account and 0.163 ms for the typical one, 53 buffers in
both cases. For an ordinary user the old index was already fast enough. What
changes is that the cost no longer grows with the number of predictions a user
has made. Plans are in `docs/explain_before.txt` and `docs/explain_after.txt`.

The index is 38 MB on the benchmark table and is maintained on every insert. It
replaces both the single-column `user_id` index and the old company/year index,
so the table carries less index than before. Inserts are batched off the
request path, see [0004](0004-batch-the-prediction-log.md).
