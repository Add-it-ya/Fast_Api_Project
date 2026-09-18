"""Fixed-window rate limiting backed by Redis.

Counters live in Redis rather than process memory so the limit still holds
when the API runs more than one replica.
"""

import time

from fastapi import Request
from redis.exceptions import RedisError

from app.cache.redis_cache import get_redis, record_redis_error
from app.core.config import settings
from app.core.exceptions import RateLimitExceededError
from app.core.security import decode_access_token


def _client_id(request: Request) -> str:
    """Who this request is counted against.

    Only a verified identity decides the bucket. The API key is the same for
    every caller, so keying on it made all users share one budget; and /login
    never checks it, so keying on whatever the header said handed out a fresh
    budget for every forged value. A forged bearer token fails signature
    verification here and falls through to the client address.
    """
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() == 'bearer' and token:
        claims = decode_access_token(token)
        if claims and isinstance(claims.get('uid'), int):
            return f'user:{claims["uid"]}'
    client = request.client
    return f'ip:{client.host if client else "unknown"}'


def _route_path(request: Request) -> str:
    """The route template, so /predictions/1/actual and /predictions/2/actual
    share a budget instead of each id getting its own."""
    route = request.scope.get('route')
    return getattr(route, 'path', request.url.path)


async def enforce_rate_limit(request: Request) -> None:
    window = settings.RATE_LIMIT_WINDOW_SECONDS
    bucket = int(time.time() // window)
    key = f'ratelimit:{_client_id(request)}:{_route_path(request)}:{bucket}'

    # One round trip instead of two. The key already embeds the time bucket, so
    # refreshing the TTL on every hit cannot extend the window.
    try:
        async with get_redis().pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, window * 2)
            count, _ = await pipe.execute()
    except RedisError:
        # Fail open. The counters are the only thing lost with Redis, and
        # refusing every login and prediction to protect them would turn a
        # cache outage into a full one. /ready and redis_errors_total still
        # show the outage, and an alert fires on the counter.
        record_redis_error('rate_limit')
        return

    if count > settings.RATE_LIMIT_REQUESTS:
        raise RateLimitExceededError(
            f'Rate limit of {settings.RATE_LIMIT_REQUESTS} requests per {window}s exceeded'
        )
