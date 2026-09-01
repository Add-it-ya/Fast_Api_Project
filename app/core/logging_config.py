"""Non-blocking, correlatable logging.

Two things beyond a default logging setup:

* Container stdout is a pipe. Writing to it with a plain StreamHandler blocks
  the calling thread, and in an async service that thread is the event loop, so
  every log line stalls every request in flight. Records go onto a queue and a
  background thread does the writing.
* Every line carries the id of the request that produced it, so a single
  request can be followed through the logs instead of guessing which
  interleaved lines belong together.
"""

import atexit
import contextvars
import json
import logging
import queue
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener

_listener: QueueListener | None = None

request_id: contextvars.ContextVar[str] = contextvars.ContextVar('request_id', default='-')

TEXT_FORMAT = '%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s'

# Attributes LogRecord always carries; anything else was passed as an extra and
# belongs in the structured output.
_STANDARD = set(logging.LogRecord('', 0, '', 0, '', (), None).__dict__) | {
    'message',
    'asctime',
    'taskName',
}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, 'request_id'):
            record.request_id = request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            'ts': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'request_id': getattr(record, 'request_id', '-'),
            'message': record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and key != 'request_id':
                payload[key] = value
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO, fmt: str = 'json') -> None:
    global _listener
    if _listener is not None:
        return

    log_queue: queue.Queue = queue.Queue(-1)

    stream = logging.StreamHandler()
    stream.setFormatter(JsonFormatter() if fmt == 'json' else logging.Formatter(TEXT_FORMAT))

    _listener = QueueListener(log_queue, stream, respect_handler_level=True)
    _listener.start()
    atexit.register(shutdown_logging)

    handler = QueueHandler(log_queue)
    # The filter belongs on the emitting side: the listener thread has no
    # access to the request's context.
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)


def shutdown_logging() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None
