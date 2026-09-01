# 0005. Index (company, year, created_at DESC) in that order

**Status:** Accepted

## Context

`GET /predictions/history` filters on company and model year and returns newest
first. On a 1,010,000-row table with only a primary key, the plan was a parallel
sequential scan discarding 336,281 rows per worker, then a top-N heapsort —
26 ms and 20,476 buffers to return 50 rows.

## Decision

One composite index with the equality columns first and the sort column last, in
the direction the query asks for:

```sql
CREATE INDEX ix_predictions_company_year_created_at
    ON predictions (company, year, created_at DESC);
```

The order is the decision. Equality predicates first means a b-tree descent
lands directly on the matching range; the sort column last means entries within
that range are already in `created_at DESC` order.

Reversing it to `(created_at, company, year)` would be close to useless: the
leading column carries no filter, so the scan could not narrow before reading.

The explicit `DESC` matters less than it looks — PostgreSQL can walk a b-tree
backwards, so an ascending index would also avoid the sort here. It is declared
in the direction the query uses to keep the intent obvious, and it starts
mattering as soon as a second sort column with a different direction appears.

## Consequences

26 ms to 0.177 ms, buffers 20,476 to 53. Both plans are committed verbatim in
`docs/explain_before.txt` and `docs/explain_after.txt`.

**The sort node disappears entirely**, which is the more interesting half. An
index making a scan faster is expected; the same index also returning rows
pre-ordered is what lets `LIMIT 50` stop after 50 rows instead of sorting all
1,157 matches.

The index costs 39 MB against a 187 MB table and is maintained on every insert.
That is a real cost on a table written to by every prediction — see
[0004](0004-batch-the-prediction-log.md), which is what makes those writes
asynchronous and batched.
