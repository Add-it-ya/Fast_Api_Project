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

The live sample is the newest predictions in the prediction log, which holds
every request's inputs already. Reading it from there gives one answer for the
whole service, whichever worker computes it, and it survives restarts.
"""

import asyncio
import logging
import math

from prometheus_client import Gauge

from app.db.repositories import PredictionRepository
from app.db.session import SessionFactory

logger = logging.getLogger(__name__)

# Every worker computes the same figures from the same rows, so 'max' just
# reports them once.
DRIFT_PSI = Gauge(
    'model_feature_drift_psi',
    'Population Stability Index of a live feature against its training distribution',
    ['feature'],
    multiprocess_mode='max',
)
DRIFT_SAMPLES = Gauge(
    'model_drift_window_samples',
    'Predictions in the current drift detection window',
    multiprocess_mode='max',
)

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


def feature_scores(reference: dict, sample: list[dict]) -> dict[str, float]:
    """PSI per feature of `sample` against the training reference."""
    scores: dict[str, float] = {}

    for column, spec in reference.get('numeric', {}).items():
        values = [row[column] for row in sample if isinstance(row.get(column), int | float)]
        if not values:
            continue
        edges = spec['edges']
        counts = [0] * (len(edges) - 1)
        for value in values:
            counts[_bucket(value, edges)] += 1
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

    return scores


def report(reference: dict, scores: dict[str, float], samples: int, min_samples: int) -> dict:
    ready = bool(reference) and samples >= min_samples and bool(scores)
    shown = scores if ready else {}
    worst = max(shown.values(), default=0.0)
    return {
        'configured': bool(reference),
        'window_samples': samples,
        'min_samples': min_samples,
        'ready': ready,
        'worst_psi': round(worst, 4),
        'status': classify(worst) if shown else 'unknown',
        'features': [
            {'feature': name, 'psi': round(value, 4), 'status': classify(value)}
            for name, value in sorted(shown.items(), key=lambda kv: -kv[1])
        ],
    }


class DriftMonitor:
    def __init__(self, window: int = 2000, min_samples: int = 200):
        self._window = window
        self._min_samples = min_samples
        self._reference: dict = {}
        self._latest = report({}, {}, 0, min_samples)

    @property
    def configured(self) -> bool:
        return bool(self._reference)

    def configure(self, training_distribution: dict) -> None:
        self._reference = training_distribution or {}
        self._latest = report(self._reference, {}, 0, self._min_samples)

    def snapshot(self) -> dict:
        return self._latest

    async def refresh(self) -> dict:
        """Score the newest `window` predictions against training."""
        if not self._reference:
            return self.snapshot()

        columns = [*self._reference.get('numeric', {}), *self._reference.get('categorical', {})]
        async with SessionFactory() as session:
            sample = await PredictionRepository(session).recent_inputs(columns, self._window)

        # Pure CPU work over a couple of thousand rows; kept off the event loop.
        scores = await asyncio.to_thread(feature_scores, self._reference, sample)
        self._latest = report(self._reference, scores, len(sample), self._min_samples)

        DRIFT_SAMPLES.set(len(sample))
        if self._latest['ready']:
            for feature, value in scores.items():
                DRIFT_PSI.labels(feature=feature).set(value)
        return self._latest


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
