"""Regression test for the non-blocking request path.

scikit-learn inference is synchronous. If it is ever called directly from an
async route instead of through asyncio.to_thread, it blocks the event loop and
concurrent requests serialise. That change would not fail any other test in
this suite - throughput is the only symptom - so it is asserted here.
"""

import asyncio
import time

from app.core.config import settings
from app.main import app

# Inference is made slow enough to dominate the per-request work that genuinely
# does run on the event loop (validation, the Redis round trip, the insert), so
# the measurement reflects the model call rather than that fixed overhead.
INFERENCE_SECONDS = 0.15
CONCURRENT_REQUESTS = 8

# Blocking inference gives a ratio near 1.0; offloading it lands near 0.3.
MAX_CONCURRENT_TO_SERIAL_RATIO = 0.6


class SlowModel:
    """Stands in for a model slow enough that serialisation is measurable."""

    def predict(self, frame):
        time.sleep(INFERENCE_SECONDS)
        return [531520.94]


async def test_concurrent_predictions_do_not_serialise(client, auth_headers, valid_car, monkeypatch):
    monkeypatch.setattr(settings, 'RATE_LIMIT_REQUESTS', 1000)
    app.state.model = SlowModel()

    async def predict(offset):
        # Distinct feature vectors so every request is a cache miss and
        # actually reaches the model.
        return await client.post('/predict', json=valid_car | {'km_driven': offset}, headers=auth_headers)

    # Measure the serial baseline in the same run rather than deriving it from
    # INFERENCE_SECONDS, so coverage tracing and CI cpu share affect both
    # halves equally and the comparison stays meaningful.
    started = time.perf_counter()
    for i in range(CONCURRENT_REQUESTS):
        assert (await predict(10_000 + i)).status_code == 200
    serial_elapsed = time.perf_counter() - started

    started = time.perf_counter()
    responses = await asyncio.gather(*[predict(20_000 + i) for i in range(CONCURRENT_REQUESTS)])
    concurrent_elapsed = time.perf_counter() - started

    assert [r.status_code for r in responses] == [200] * CONCURRENT_REQUESTS
    assert all(r.json()['cached'] is False for r in responses)

    ratio = concurrent_elapsed / serial_elapsed
    assert ratio < MAX_CONCURRENT_TO_SERIAL_RATIO, (
        f'{CONCURRENT_REQUESTS} predictions took {concurrent_elapsed:.2f}s concurrently '
        f'versus {serial_elapsed:.2f}s one at a time (ratio {ratio:.2f}). Concurrency is '
        'buying almost nothing, so inference is most likely blocking the event loop again.'
    )


async def test_event_loop_stays_responsive_during_inference(client, auth_headers, valid_car, monkeypatch):
    monkeypatch.setattr(settings, 'RATE_LIMIT_REQUESTS', 1000)
    app.state.model = SlowModel()

    prediction = asyncio.create_task(client.post('/predict', json=valid_car, headers=auth_headers))
    await asyncio.sleep(INFERENCE_SECONDS / 5)

    started = time.perf_counter()
    health = await client.get('/health')
    health_latency = time.perf_counter() - started

    assert health.status_code == 200
    assert health_latency < INFERENCE_SECONDS, (
        f'/health took {health_latency:.3f}s while a prediction was in flight; '
        'the event loop is blocked during inference.'
    )
    await prediction
