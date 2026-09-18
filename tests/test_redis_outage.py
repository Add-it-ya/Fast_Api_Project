"""Losing Redis should cost speed, not availability.

Redis holds the prediction cache and the rate-limit counters. Neither is the
source of truth for anything, so an outage should degrade the service - every
prediction becomes a model call, and the limiter stops limiting - rather than
fail requests that have nothing to do with caching.
"""

import pytest
import redis.asyncio as aioredis
from prometheus_client import REGISTRY

from app.cache import redis_cache


def _errors(operation: str) -> float:
    return REGISTRY.get_sample_value('redis_errors_total', {'operation': operation}) or 0.0


@pytest.fixture
async def break_redis(monkeypatch):
    """Swap in a real client pointed at a port nothing listens on, so the
    failure is a genuine connection error rather than a mock's idea of one.

    Returned as a function so a test can finish its setup - registering,
    logging in - against the working Redis first.
    """
    dead = aioredis.from_url('redis://127.0.0.1:1/0', socket_connect_timeout=0.5, decode_responses=True)

    def _break() -> None:
        monkeypatch.setattr(redis_cache, '_redis', dead)

    yield _break
    await dead.aclose()


async def test_predictions_fall_back_to_the_model(client, auth_headers, valid_car, break_redis):
    break_redis()

    response = await client.post('/predict', json=valid_car, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()['cached'] is False
    assert float(response.json()['predicted_price'].replace(',', '')) > 0


async def test_login_still_works(client, registered_user, break_redis):
    break_redis()

    response = await client.post('/login', json=registered_user)

    assert response.status_code == 200


async def test_the_outage_is_still_visible(client, registered_user, break_redis):
    """Failing open is only acceptable if somebody can see it happening."""
    before = _errors('rate_limit')
    break_redis()

    await client.post('/login', json=registered_user)
    ready = await client.get('/ready')

    assert ready.status_code == 503
    assert ready.json()['checks']['redis'] is False
    assert _errors('rate_limit') == before + 1
