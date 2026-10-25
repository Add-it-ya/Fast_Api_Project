# Design decisions

The decisions that shaped the service, each with what was chosen, why, what it
costs, and what was measured. They were written up after the code was built,
from the measurements and git history of the time, so read them as an account
of the reasoning rather than as records kept while deciding. Where a number
has raw output behind it, the link points to it.

1. [Two credentials, one job each](#1-two-credentials-one-job-each)
2. [Inference in a thread, one core per prediction](#2-inference-in-a-thread-one-core-per-prediction)
3. [A prediction cache in Redis](#3-a-prediction-cache-in-redis)
4. [Redis is optional](#4-redis-is-optional)
5. [PostgreSQL](#5-postgresql)
6. [The prediction log is written in batches](#6-the-prediction-log-is-written-in-batches)
7. [`/predict` trusts the token](#7-predict-trusts-the-token)
8. [History index: `(user_id, created_at DESC)`](#8-history-index-user_id-created_at-desc)
9. [Choosing the model by cross-validation](#9-choosing-the-model-by-cross-validation)
10. [Drift by PSI, from the prediction log](#10-drift-by-psi-from-the-prediction-log)

---

## 1. Two credentials, one job each

**Decision.** Users register, log in and send a JWT. The API key is the
operator credential. It guards closed registration, reporting a car's real
sale price, and forcing a drift recompute. `/predict` and history need only
the user's token.

**Why.** Each check then has a reason. Reporting a sale price is not something
a user should do: letting any user set it would let them skew the live error
figures. A recompute costs CPU, so it should not be open to anyone.

**Cost.** One shared operator key, read from the environment. Rotating it means
redeploying. Per-operator keys would fix that and are not needed yet.

## 2. Inference in a thread, one core per prediction

**Decision.** `asyncio.to_thread` runs scikit-learn's `predict` off the event
loop. The forest is set to `n_jobs=1` when loaded.

**Why.** `predict` is synchronous. Called directly from an `async def` route it
holds the event loop, and every other request on that worker waits.
`tests/test_concurrency.py` fails if inference ever goes back on the loop.

Training uses every core (`n_jobs=-1`), and the saved model keeps that setting.
For a single-row prediction, spreading the trees across 12 cores was 7x
slower than using one: 30 ms against 4.3 ms for v3, 18 ms against 3.1 ms for v4
([benchmarks/inference](../benchmarks/inference/n_jobs.txt)).

**Cost.** Throughput is bounded by the default thread pool and the GIL: this is
concurrency, not parallelism. One worker tops out near 95 model calls a second
([benchmarks/v4](../benchmarks/README.md)). Above about 25 clients on 4
workers, throughput falls, and too many inference threads competing for the
cores is the first suspect. That is not yet diagnosed.

**Not chosen.** A process pool would pickle a DataFrame across a process
boundary on every call. A separate inference service would add a network hop
and a deployment for a model that answers in about 3 ms.

## 3. A prediction cache in Redis

**Decision.** Cache each prediction under a SHA-256 of its features and the
model version, in Redis, for an hour.

**Why.** The model is deterministic, so a repeated feature vector is wasted
work. With 4 workers, an in-process cache would give each worker its own copy
and a quarter of the hits. The model version is in the key, so a retrained
model never answers from its predecessor's cache.

**Measured.** At one client a hit takes 3.7 ms end to end and a miss 10.1 ms;
at ten clients 14.9 against 36.5 ms ([benchmarks/cache](../benchmarks/cache/README.md)).
A few milliseconds per hit, worth keeping, not an order of magnitude.

**Cost.** The benefit depends entirely on the hit ratio, and nobody has measured
a real one. Kilometres driven is one of the twelve features, so an exact repeat
needs the same car described the same way. The load test's 80% hit ratio comes
from replaying a pool of 200 cars and says nothing about real traffic.

## 4. Redis is optional

**Decision.** If Redis fails, a cache read counts as a miss, a cache write is
skipped, and the rate limiter lets the request through. Short timeouts
(0.25 s to connect, 0.5 s per command) stop a hung Redis from holding requests.

**Why.** Redis holds nothing that is the source of truth. Refusing every login
and prediction to protect the counters would turn a cache outage into a full
outage.

**Cost.** During an outage nothing is rate limited. That is visible:
`/ready` reports Redis down, `redis_errors_total` counts every failure, and the
`RedisDegraded` alert fires on it. Predictions also get slower, because each of
the three Redis calls in a prediction waits out its timeout before giving up.

## 5. PostgreSQL

**Decision.** PostgreSQL 16 through async SQLAlchemy and `asyncpg`, with schema
changes in Alembic migrations that run when the container starts.

**Why.** Four workers write prediction rows at the same time, and SQLite
serialises writers. The history query needs a composite index with a
descending sort column and plans checked with `EXPLAIN (ANALYZE, BUFFERS)`. The
seed script loads benchmark data with `COPY`, 15x faster than row-by-row
`INSERT` ([benchmarks/seed](../benchmarks/seed/copy_vs_insert.txt)).

**Cost.** A server to run, health-check and connect to. Connection pools are
per worker, so `WEB_CONCURRENCY x (DB_POOL_SIZE + DB_MAX_OVERFLOW)` must stay
under `max_connections`. Exceeding it shows up as
`asyncpg.TooManyConnectionsError` under load, not at startup. That happened
while tuning the worker count.

## 6. The prediction log is written in batches

**Decision.** `/predict` puts its row on an in-memory queue and returns. A single
consumer writes the queue in batches of up to 200, as one multi-row `INSERT`.

**Why.** A commit per request put a database round trip on every prediction. A
per-request `BackgroundTasks` was tried first and was slower, because each task
opened its own session and they fought over the pool. Deferring work did not
reduce it; batching does. That comparison was made on the original 10-tree
model and its output was not kept.

**Cost.**

- Rows still queued when the process dies are lost. That is acceptable only
  because the row is an analytics record: the response never says it was
  saved.
- The queue holds 20,000 rows. When it is full, rows are dropped rather than
  slowing the request, and `prediction_log_rows_dropped_total` counts them.
- A multi-row `INSERT` is all or nothing. If the database rejects a batch,
  the writer retries it row by row, so one bad row (for example a deleted
  user's foreign key) loses only itself.
- `/predict` returns its `prediction_id` before the row exists, so reporting a
  sale price a moment later can briefly get a 404 that says to retry.

## 7. `/predict` trusts the token

**Decision.** `/predict` builds the caller's identity from the JWT's claims
without looking the user up. `/me` still loads the stored record.

**Why.** A lookup on every request is a database round trip for information the
signed token already carries.

**Cost.** A user deleted while their token is valid keeps access until it
expires, up to 30 minutes. A Redis revocation list would close that, at the
price of a Redis call on every request.

## 8. History index: `(user_id, created_at DESC)`

**Decision.** History returns the caller's own predictions newest first, served
by one composite index with the equality column first and the sort column last.

**Why.** With an index on `user_id` alone, Postgres fetched every one of the
user's rows and sorted them. On 1,000,000 rows that was 62.8 ms for an account
with 200,000 predictions and 2.0 ms for one with 1,000. With the composite
index it is 0.196 ms and 0.163 ms, 53 buffers either way
([query_optimisation.md](query_optimisation.md)). For an ordinary user the old
index was fast enough. The new one stops the cost growing with the user's
history.

**Cost.** 38 MB on the benchmark table, maintained on every insert. It replaced
two indexes, so the table carries less index than before.

An earlier version of history searched every user's predictions by brand and
year, behind a `(company, year, created_at DESC)` index. No user needed that
query, and it showed other users' activity, so it was replaced.

## 9. Choosing the model by cross-validation

**Decision.** A random forest, 50 trees at depth 12, picked by `training/sweep.py`.

**Why.** The sweep scores 20 configurations with 5-fold cross-validation on the
training rows only, then takes the smallest artifact whose CV error is within
one standard error of the best. The rule was fixed before the results were
seen. The best CV score needed a 15.7 MB forest; the pick is 1.7 MB and within
₹854 of it ([model_card.md](model_card.md#choosing-the-forest)).

The previous model was sized by scoring candidates on the test rows, which made
its reported error optimistic. The test rows are now scored once, after the
choice is made.

**Cost.** A random split, not a time-based one, so the metrics say nothing about
how the model ages. MAPE is about 18%.

## 10. Drift by PSI, from the prediction log

**Decision.** Population Stability Index per feature, comparing the newest
2,000 predictions in the prediction log with the training distribution saved at
training time. A background task recomputes it every minute and exports it to
Prometheus.

**Why.** Accuracy can only be measured once real sale prices come back, but the
inputs shift straight away. PSI handles numeric and categorical features with
one formula and needs only bin proportions from training. Every request's
features are already in the log, so every worker computes the same answer from
the same rows, and it survives restarts. An earlier version kept a window in
each worker's memory, so each scored a quarter of the traffic and lost it on
restart.

Two corrections were needed before it read stable traffic as stable:

- Brands under 1% of training rows are pooled with anything unseen. Without
  that, one occurrence of a rare brand sent `ln(actual/expected)` soaring.
- A numeric feature with no more distinct values than there are bins, such as
  `seats`, is treated as categorical. Quantile bins collapsed on it.

**Cost.** The window is a count, not a time span, so with little traffic it can
reach back weeks. Fewer than 200 rows reports "not ready", because PSI on a
small sample is noise. Rows count only once the batching writer has flushed
them.
