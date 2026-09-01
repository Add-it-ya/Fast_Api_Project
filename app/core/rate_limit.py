"""Fixed-window rate limiting backed by Redis.

Counters live in Redis rather than process memory so the limit still holds
when the API runs more than one replica.
"""

import time

from fastapi import Request

from app.cache.redis_cache import get_redis
from app.core.config import settings
from app.core.exceptions import RateLimitExceededError


def _client_id(request: Request) -> str:
    api_key = request.headers.get('api-key')
    if api_key:
        return f'key:{api_key[:16]}'
    client = request.client
    return f'ip:{client.host if client else "unknown"}'


async def enforce_rate_limit(request: Request) -> None:
    window = settings.RATE_LIMIT_WINDOW_SECONDS
    bucket = int(time.time() // window)
    key = f'ratelimit:{_client_id(request)}:{request.url.path}:{bucket}'

    # One round trip instead of two. The key already embeds the time bucket, so
    # refreshing the TTL on every hit cannot extend the window.
    async with get_redis().pipeline(transaction=False) as pipe:
        pipe.incr(key)
        pipe.expire(key, window * 2)
        count, _ = await pipe.execute()

    if count > settings.RATE_LIMIT_REQUESTS:
        raise RateLimitExceededError(
            f'Rate limit of {settings.RATE_LIMIT_REQUESTS} requests per {window}s exceeded'
        )
