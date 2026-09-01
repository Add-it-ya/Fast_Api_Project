# 0006. Authenticate /predict from token claims, no database lookup

**Status:** Accepted

## Context

`get_current_user` loaded the user row on every authenticated request to confirm
they still existed. Measured against `/health`, that lookup cost about 2.2 ms
per request — on a path where a cache hit is around 8 ms end to end, it was a
meaningful share of the work, and it put the database on every request whether
or not the handler needed it.

## Decision

`/predict` depends on a `Principal` built from the token's `uid` and `sub`
claims, with no database access. Endpoints that need the stored record —
`/me` — keep using `get_current_user`.

This is what a stateless JWT is for: the token is the assertion, and verifying
its signature is the check.

## Consequences

A database round trip leaves the hot path.

**A user deleted mid-token keeps access until the token expires**, up to 30
minutes. That is inherent to stateless JWT auth rather than a shortcut, and the
mitigations if it ever matters are a short expiry (already 30 minutes), a
revocation list checked in Redis, or reverting to a lookup on the endpoints that
warrant it.

A prediction is logged against `principal.id` without confirming the row exists.
If that user was deleted, the insert violates the foreign key — the batching
writer logs the failure rather than failing the request, since the caller was
never promised the row.
