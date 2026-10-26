"""Regression test for the non-blocking request path.

scikit-learn inference is synchronous. If it is ever called directly from an
async route instead of through asyncio.to_thread, it blocks the event loop and
concurrent requests serialise. That change would not fail any other test in
this suite - throughput is the only symptom - so it is asserted here.
"""

import asyncio
import time

import pytest

from app.core.config import settings
from app.core.security import hash_password
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


async def longest_loop_stall(request) -> float:
    """Run `request` while a heartbeat ticks every few milliseconds, and return
    the longest gap between ticks. If something blocks the event loop, that gap
    is the whole blocking call."""
    gaps: list[float] = []
    finished = asyncio.Event()

    async def heartbeat():
        last = time.perf_counter()
        while not finished.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    try:
        await request
    finally:
        finished.set()
        await beat
    return max(gaps)


async def test_event_loop_stays_responsive_during_inference(client, auth_headers, valid_car, monkeypatch):
    # Measured with a heartbeat rather than by timing a second request: a
    # request started after a short sleep only ran once a blocking prediction
    # had finished, so that version passed whether or not inference blocked.
    monkeypatch.setattr(settings, 'RATE_LIMIT_REQUESTS', 1000)
    app.state.model = SlowModel()

    stall = await longest_loop_stall(client.post('/predict', json=valid_car, headers=auth_headers))

    assert stall < INFERENCE_SECONDS / 2, (
        f'the event loop stalled for {stall:.3f}s during a prediction, against '
        f'{INFERENCE_SECONDS:.3f}s of inference - the model is running on the loop.'
    )


@pytest.mark.parametrize(
    'path,credentials',
    [
        ('/register', {'username': 'newcomer', 'password': 'test-password-1'}),
        ('/login', {'username': 'tester', 'password': 'test-password-1'}),
        # Unknown users are hashed against a dummy so timing does not reveal
        # which usernames exist; that hash must not block either.
        ('/login', {'username': 'nobody', 'password': 'test-password-1'}),
    ],
)
async def test_event_loop_stays_responsive_during_password_hashing(
    client, registered_user, path, credentials
):
    """bcrypt is slow on purpose. Run on the event loop, one login would stall
    every other request in the worker for its whole duration."""
    started = time.perf_counter()
    hash_password('calibration-password')
    bcrypt_seconds = time.perf_counter() - started

    stall = await longest_loop_stall(client.post(path, json=credentials))

    assert stall < bcrypt_seconds / 2, (
        f'the event loop stalled for {stall:.3f}s during {path}, against '
        f'{bcrypt_seconds:.3f}s for one bcrypt hash - hashing is blocking the loop.'
    )
