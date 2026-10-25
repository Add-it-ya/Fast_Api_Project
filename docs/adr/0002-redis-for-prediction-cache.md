# 0002. Cache predictions in Redis, not in process

**Status:** Accepted

## Context

The model is deterministic: the same twelve features always produce the same
price, so computing a repeated feature vector again is wasted work. How often
real traffic repeats an exact vector is unknown. Kilometres driven is part of
it, so a repeat needs the same car described the same way, for example one
listing priced again.

## Decision

Cache on a SHA-256 of the feature vector in Redis, with a TTL.

An in-process `lru_cache` would be faster per lookup and free of a network hop.
It was rejected because the service runs four workers: each would keep its own
copy, so the effective hit rate would be roughly a quarter of what a shared
cache gives, and memory use four times higher. A restart would also empty it.

## Consequences

Measured with model v4 on 4 workers ([benchmarks/cache](../../benchmarks/cache/)),
median end-to-end latency for a hit is 3.7 ms against 10.1 ms for a miss at one
client, and 14.9 ms against 36.5 ms at ten. A hit saves the model call, the
DataFrame it needs and the thread hand-off. That is worth keeping, but it is a
few milliseconds, not an order of magnitude.

The benefit scales with the hit ratio, and the load test sets that itself by
replaying a fixed pool of vectors. A real hit ratio would have to be read off
`predictions_total{cache=...}` in production. The two outcomes are measured
separately, `prediction_latency_seconds` is labelled by outcome, because a
blended p95 mostly reports the hit ratio.

Redis is on the read path but is not required for it: a cache error counts as
a miss and the model answers, and `redis_errors_total` records the failure.

Cached values are JSON. The original implementation round-tripped them through
`str()` and `eval()`, which executes whatever is in the cache — anything able to
write to Redis could run code in the API process.
