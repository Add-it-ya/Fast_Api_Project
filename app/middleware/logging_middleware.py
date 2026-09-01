import logging
import time

logger = logging.getLogger('api.access')


class LoggingMiddleware:
    """Pure ASGI access logging.

    Written against the raw ASGI interface rather than BaseHTTPMiddleware,
    which wraps every request in an anyio task group and shows up clearly in
    the benchmarks. One line per request, not one per phase.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.info('%s %s %s %.1fms', scope['method'], scope['path'], status, duration_ms)
