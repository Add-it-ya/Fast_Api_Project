"""Async Redis cache for prediction results.

Values are stored as JSON. The previous implementation round-tripped them
through str()/eval(), which executes whatever is in the cache - anything able
to write to Redis could run code in the API process.
"""

import hashlib
import json

import redis.asyncio as aioredis

from app.core.config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def build_cache_key(features: dict, model_version: int | None) -> str:
    payload = json.dumps(features, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    # The model version is part of the key, so a retrained model starts with a
    # cold cache instead of its predecessor answering for it until the TTL runs
    # out.
    return f'prediction:v{model_version or 0}:{digest}'


async def get_cached_prediction(key: str) -> float | None:
    raw = await get_redis().get(key)
    if raw is None:
        return None
    try:
        return float(json.loads(raw)['predicted_price'])
    except (ValueError, KeyError, TypeError):
        return None


async def set_cached_prediction(key: str, value: float) -> None:
    await get_redis().set(
        key,
        json.dumps({'predicted_price': float(value)}),
        ex=settings.PREDICTION_CACHE_TTL_SECONDS,
    )
