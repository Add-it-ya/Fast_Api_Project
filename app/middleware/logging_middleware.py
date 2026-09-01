import logging
import time
import uuid

from app.core.logging_config import request_id

logger = logging.getLogger('api.access')

REQUEST_ID_HEADER = b'x-request-id'


class LoggingMiddleware:
    """Pure ASGI access logging with request correlation.

    Written against the raw ASGI interface rather than BaseHTTPMiddleware,
    which wraps every request in an anyio task group and shows up clearly in
    the benchmarks. One line per request, not one per phase.

    An inbound X-Request-ID is honoured so a trace survives across services;
    otherwise one is generated. Either way it goes back on the response, so a
    caller reporting a problem can quote the id.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get('headers') or [])
        incoming = headers.get(REQUEST_ID_HEADER)
        correlation = incoming.decode('latin-1')[:64] if incoming else uuid.uuid4().hex[:16]
        token = request_id.set(correlation)

        started = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                message.setdefault('headers', [])
                message['headers'].append((REQUEST_ID_HEADER, correlation.encode('latin-1')))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.info(
                '%s %s %s %.1fms',
                scope['method'],
                scope['path'],
                status,
                duration_ms,
                extra={
                    'method': scope['method'],
                    'path': scope['path'],
                    'status': status,
                    'duration_ms': round(duration_ms, 2),
                },
            )
            request_id.reset(token)
