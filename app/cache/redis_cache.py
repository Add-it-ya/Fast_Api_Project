"""Async Redis cache for prediction results.

Values are stored as JSON. The previous implementation round-tripped them
through str()/eval(), which executes whatever is in the cache - anything able
to write to Redis could run code in the API process.

Redis is treated as an accelerator, not a dependency: a failed read is a miss
and a failed write is skipped, so an outage makes predictions slower rather
than making them fail.
"""

import hashlib
import json
import logging
import time

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.metrics import REDIS_ERRORS

logger = logging.getLogger(__name__)

# Without these a Redis host that stops answering, rather than refusing, holds
# every request for the operating system's TCP timeout - minutes, not seconds.
# Redis sits on the same private network and every command here is
# sub-millisecond when it is healthy, so these leave a wide margin. They are
# also what an outage costs: a prediction makes three Redis calls, and with
# Redis down each one waits out the connect timeout before degrading.
CONNECT_TIMEOUT_SECONDS = 0.25
COMMAND_TIMEOUT_SECONDS = 0.5

# During an outage every request fails the same way. One line per interval says
# so; the counter carries the volume.
ERROR_LOG_INTERVAL_SECONDS = 30.0

_redis: aioredis.Redis | None = None
_last_error_logged: dict[str, float] = {}


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
            socket_timeout=COMMAND_TIMEOUT_SECONDS,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def record_redis_error(operation: str) -> None:
    """Count a Redis failure the caller has chosen to survive. Call from
    inside the except block so the logged line carries the traceback."""
    REDIS_ERRORS.labels(operation=operation).inc()
    now = time.monotonic()
    if now - _last_error_logged.get(operation, float('-inf')) >= ERROR_LOG_INTERVAL_SECONDS:
        _last_error_logged[operation] = now
        logger.warning('Redis unavailable during %s; continuing without it', operation, exc_info=True)


def build_cache_key(features: dict, model_version: int | None) -> str:
    payload = json.dumps(features, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    # The model version is part of the key, so a retrained model starts with a
    # cold cache instead of its predecessor answering for it until the TTL runs
    # out.
    return f'prediction:v{model_version or 0}:{digest}'


async def get_cached_prediction(key: str) -> float | None:
    try:
        raw = await get_redis().get(key)
    except RedisError:
        record_redis_error('cache_get')
        return None
    if raw is None:
        return None
    try:
        return float(json.loads(raw)['predicted_price'])
    except (ValueError, KeyError, TypeError):
        return None


async def set_cached_prediction(key: str, value: float) -> None:
    try:
        await get_redis().set(
            key,
            json.dumps({'predicted_price': float(value)}),
            ex=settings.PREDICTION_CACHE_TTL_SECONDS,
        )
    except RedisError:
        record_redis_error('cache_set')
