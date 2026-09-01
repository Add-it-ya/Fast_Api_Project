# 0004. Batch prediction-log writes off the request path

**Status:** Accepted

## Context

Every prediction was inserted and committed inside the request, putting a
database round trip and a commit on the critical path. The caller never reads
the row back and is never told it was saved — it is an analytics record, not
part of the response.

## Decision

Rows go onto an in-memory queue. A single consumer drains them and writes each
batch as one multi-row `INSERT`.

**A per-request `BackgroundTasks` was tried first and measured worse**: p95 at
100-way concurrency went to 3.2 s, because every task opened its own session and
they fought over the connection pool. Deferring work is not the same as reducing
it. Batching turns N commits into roughly N/200.

## Consequences

The write leaves the request path, and the database sees far fewer transactions
under load.

**Rows still queued when the process dies are lost.** That is the trade, and it
is only acceptable because nothing tells the caller the row was saved. It would
be unacceptable for anything the response acknowledges.

The queue is bounded at 20,000. When full, rows are dropped rather than blocking
the request that produced them — shedding a log row beats slowing down a user.
`prediction_log_rows_dropped_total` counts it and an alert fires on any
sustained shedding.

Tests that assert on persisted rows must drain the queue first, via the
`flush_predictions` fixture. A test that queries immediately after a request
races the writer.
