"""Application metrics.

The Prometheus instrumentator already records generic HTTP timings. What it
cannot see is anything about the prediction itself: whether the cache answered,
how long the model took as distinct from the request around it, or what is
being asked about. Those are the numbers that tell you whether the service is
healthy as an ML service rather than as a web server.
"""

from prometheus_client import Counter, Histogram

# Sub-millisecond buckets matter here: a cache hit is expected to land near
# 1 ms, and the default instrumentator buckets start too coarse to show it.
LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

PREDICTION_LATENCY = Histogram(
    'prediction_latency_seconds',
    'End-to-end latency of a prediction, split by cache outcome',
    ['cache'],
    buckets=LATENCY_BUCKETS,
)

INFERENCE_LATENCY = Histogram(
    'model_inference_seconds',
    'Time inside the model, excluding cache lookup and request overhead',
    buckets=LATENCY_BUCKETS,
)

PREDICTIONS = Counter(
    'predictions_total',
    'Predictions served',
    ['company', 'cache'],
)

PREDICTION_VALUE = Histogram(
    'prediction_value_rupees',
    'Distribution of predicted prices, to catch the model drifting as a whole',
    buckets=(1e5, 2.5e5, 5e5, 7.5e5, 1e6, 1.5e6, 2e6, 3e6, 5e6, 1e7),
)
