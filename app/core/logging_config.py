"""Non-blocking logging setup.

Container stdout is a pipe. Writing to it with a plain StreamHandler blocks the
calling thread, and in an async service that thread is the event loop, so every
log line stalls every request in flight. Records go onto a queue instead and a
background thread does the writing.
"""

import atexit
import logging
import queue
from logging.handlers import QueueHandler, QueueListener

_listener: QueueListener | None = None

FORMAT = '%(asctime)s %(levelname)s %(name)s %(message)s'


def configure_logging(level: int = logging.INFO) -> None:
    global _listener
    if _listener is not None:
        return

    log_queue: queue.Queue = queue.Queue(-1)

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(FORMAT))

    _listener = QueueListener(log_queue, stream, respect_handler_level=True)
    _listener.start()
    atexit.register(shutdown_logging)

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.addHandler(QueueHandler(log_queue))
    root.setLevel(level)


def shutdown_logging() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None
