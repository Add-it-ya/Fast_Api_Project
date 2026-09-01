# 0002. Cache predictions in Redis, not in process

**Status:** Accepted

## Context

The model is deterministic: the same twelve features always produce the same
price. Repeated feature vectors are therefore pure waste, and in a used-car
marketplace repetition is common — popular models in popular configurations get
priced over and over.

## Decision

Cache on a SHA-256 of the feature vector in Redis, with a TTL.

An in-process `lru_cache` would be faster per lookup and free of a network hop.
It was rejected because the service runs four workers: each would keep its own
copy, so the effective hit rate would be roughly a quarter of what a shared
cache gives, and memory use four times higher. A restart would also empty it.

## Consequences

Measured hit latency is around 8 ms against roughly 240 ms for a miss, so the
cache is worth having and the two are worth measuring separately —
`prediction_latency_seconds` is labelled by outcome for that reason. A blended
p95 mostly reports the hit ratio rather than anything about the service.

Redis becomes a dependency of the read path. It is checked by `/ready`, and a
Redis outage currently fails the prediction rather than degrading to a direct
model call. That is a gap, and the fix is small: treat a cache error as a miss.

Cached values are JSON. The original implementation round-tripped them through
`str()` and `eval()`, which executes whatever is in the cache — anything able to
write to Redis could run code in the API process.
