# 0007. Population Stability Index for input drift

**Status:** Accepted

## Context

A model degrades silently when live traffic stops resembling its training data.
Accuracy cannot show this until real outcomes come back — weeks, for used-car
sales — but the input distribution shifts immediately, so it is the earliest
signal available.

## Decision

Compute PSI per feature against the distribution recorded at training time,
export it to Prometheus, and read it on the conventional thresholds: under 0.1
stable, 0.1–0.25 moderate, above 0.25 significant.

PSI was chosen over a Kolmogorov–Smirnov test because it handles categorical and
numeric features with the same formula, needs only bin proportions rather than
retained samples, and produces a number practitioners already know how to read.

## Consequences

Drift is visible before accuracy moves. `scripts/drift_demo.py` demonstrates it:
traffic drawn from the training distribution reads 0.048 (stable), traffic
skewed to newer luxury cars reads 3.26 (significant).

**Two corrections were needed before it was trustworthy**, both found by testing
it against traffic that should have read as stable:

- Rare training categories broke it. 19 of 30 companies are under 1% of the
  training rows, some at 0.02%; with a few hundred samples the expected count is
  well under one, so a single occurrence sent `ln(actual/expected)` to the moon
  and ordinary traffic scored as significant drift. Categories under 1% are now
  pooled into one bucket with anything unseen.
- Near-discrete numerics broke it. `seats` has nine distinct values, so quantile
  binning collapsed to three bins with 78% of the mass in one. Any numeric with
  no more distinct values than there are bins is now treated as discrete.

`min_samples` is 200. PSI on a small window is noise, not measurement.

Each worker keeps its own window in memory, so Prometheus sees one series per
worker. Each is an independent estimate from that worker's share of traffic;
alert on the maximum. Sharing the window through Redis would unify it at the
cost of putting a write back on the request path that
[0004](0004-batch-the-prediction-log.md) worked to remove.
