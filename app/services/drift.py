"""Input drift detection using Population Stability Index.

A model silently gets worse when live traffic stops looking like its training
data. Accuracy metrics cannot show that until real outcomes arrive - often
weeks later - but the input distribution shifts immediately, so it is the
earliest signal available.

PSI compares the share of traffic falling in each bin against the share of
training rows in that bin:

    PSI = sum over bins of (actual% - expected%) * ln(actual% / expected%)

The conventional reading is < 0.1 stable, 0.1-0.25 moderate shift,
> 0.25 significant shift.
"""

import logging
import math
from collections import deque
from threading import Lock

from prometheus_client import Gauge

logger = logging.getLogger(__name__)

DRIFT_PSI = Gauge(
    'model_feature_drift_psi',
    'Population Stability Index of a live feature against its training distribution',
    ['feature'],
)
DRIFT_SAMPLES = Gauge('model_drift_window_samples', 'Observations in the current drift detection window')

STABLE = 0.1
SIGNIFICANT = 0.25
# Floor for empty bins - ln(0) is undefined, and a single unseen value should
# not send PSI to infinity.
EPSILON = 1e-6
# Categories rarer than this in training are pooled. Below roughly this share
# the expected count in a sampling window is under one, and PSI stops being a
# measurement and starts being noise.
MIN_EXPECTED_SHARE = 0.01


def classify(psi: float) -> str:
    if psi < STABLE:
        return 'stable'
    if psi < SIGNIFICANT:
        return 'moderate'
    return 'significant'


def psi(expected: list[float], actual: list[float]) -> float:
    total = 0.0
    for e, a in zip(expected, actual, strict=True):
        e = max(e, EPSILON)
        a = max(a, EPSILON)
        total += (a - e) * math.log(a / e)
    return total


class DriftMonitor:
    def __init__(self, window: int = 2000, min_samples: int = 200, recompute_every: int = 100):
        self._observations: deque[dict] = deque(maxlen=window)
        self._reference: dict = {}
        self._latest: dict = {}
        self._seen_since_refresh = 0
        self._min_samples = min_samples
        self._recompute_every = recompute_every
        self._lock = Lock()

    @property
    def configured(self) -> bool:
        return bool(self._reference)

    def configure(self, training_distribution: dict) -> None:
        with self._lock:
            self._reference = training_distribution or {}
            self._observations.clear()
            self._latest = {}
            self._seen_since_refresh = 0

    def observe(self, features: dict) -> None:
        if not self._reference:
            return
        with self._lock:
            self._observations.append(features)
            self._seen_since_refresh += 1
            due = (
                self._seen_since_refresh >= self._recompute_every
                and len(self._observations) >= self._min_samples
            )
        if due:
            self.refresh()

    def refresh(self) -> dict:
        with self._lock:
            sample = list(self._observations)
            reference = self._reference
            self._seen_since_refresh = 0

        if not reference or len(sample) < self._min_samples:
            return self.snapshot()

        scores: dict[str, float] = {}

        for column, spec in reference.get('numeric', {}).items():
            values = [row[column] for row in sample if isinstance(row.get(column), int | float)]
            if not values:
                continue
            edges = spec['edges']
            counts = [0] * (len(edges) - 1)
            for value in values:
                index = _bucket(value, edges)
                counts[index] += 1
            actual = [c / len(values) for c in counts]
            scores[column] = psi(spec['proportions'], actual)

        for column, expected_map in reference.get('categorical', {}).items():
            values = [row[column] for row in sample if row.get(column) is not None]
            if not values:
                continue

            # Categories the model barely saw in training have expected
            # proportions near zero, so a single occurrence sends ln(a/e) to
            # the moon. Collapse them, and anything unseen, into one bucket.
            categories = [c for c, p in expected_map.items() if p >= MIN_EXPECTED_SHARE]
            other_expected = sum(p for p in expected_map.values() if p < MIN_EXPECTED_SHARE)

            observed = dict.fromkeys(categories, 0)
            other = 0
            for value in values:
                key = value if value in observed else str(value)
                if key in observed:
                    observed[key] += 1
                else:
                    other += 1

            total = len(values)
            expected = [expected_map[c] for c in categories] + [other_expected]
            actual = [observed[c] / total for c in categories] + [other / total]
            scores[column] = psi(expected, actual)

        with self._lock:
            self._latest = {k: round(v, 4) for k, v in scores.items()}

        for feature, value in scores.items():
            DRIFT_PSI.labels(feature=feature).set(value)
        DRIFT_SAMPLES.set(len(sample))

        return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            scores = dict(self._latest)
            samples = len(self._observations)

        worst = max(scores.values(), default=0.0)
        return {
            'configured': bool(self._reference),
            'window_samples': samples,
            'min_samples': self._min_samples,
            'ready': samples >= self._min_samples and bool(scores),
            'worst_psi': round(worst, 4),
            'status': classify(worst) if scores else 'unknown',
            'features': [
                {'feature': name, 'psi': value, 'status': classify(value)}
                for name, value in sorted(scores.items(), key=lambda kv: -kv[1])
            ],
        }


def _bucket(value: float, edges: list[float]) -> int:
    """Index of the bin a value falls in, with the tails clamped inward."""
    if value <= edges[0]:
        return 0
    if value >= edges[-1]:
        return len(edges) - 2
    low, high = 0, len(edges) - 1
    while low < high - 1:
        mid = (low + high) // 2
        if value < edges[mid]:
            high = mid
        else:
            low = mid
    return low


monitor = DriftMonitor()
