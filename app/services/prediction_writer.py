"""Batching writer for the prediction log.

A prediction row is an analytics record: the caller does not read it back and
does not need it durable before the response is sent. Writing one row per
request put a database round trip and a commit on the critical path, and doing
it as a per-request background task was worse still, because every task opened
its own session and fought over the connection pool.

Rows go onto an in-memory queue instead, and a single consumer drains them into
one multi-row INSERT. Under load that turns N commits into roughly N/batch_size.

The trade-off is that rows still queued when the process dies are lost. That is
acceptable for an analytics log and would not be for anything the caller is
told was saved.
"""

import asyncio
import contextlib
import logging

from app.db.repositories import PredictionRepository
from app.db.session import SessionFactory

logger = logging.getLogger(__name__)


class PredictionWriter:
    def __init__(self, batch_size: int = 200, max_queue: int = 20_000):
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None
        self._batch_size = batch_size
        self.dropped = 0
        self.written = 0

    def enqueue(self, row: dict) -> None:
        """Never blocks and never raises. Shedding a log row is preferable to
        slowing down or failing the request that produced it."""
        try:
            self._queue.put_nowait(row)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % 1000 == 1:
                logger.warning('Prediction log queue full, dropped %d rows', self.dropped)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name='prediction-writer')

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        await self._drain()

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < self._batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._flush(batch)

    async def _drain(self) -> None:
        batch = []
        while not self._queue.empty():
            batch.append(self._queue.get_nowait())
        await self._flush(batch)

    async def _flush(self, batch: list[dict]) -> None:
        if not batch:
            return
        try:
            async with SessionFactory() as session:
                await PredictionRepository(session).log_many(batch)
            self.written += len(batch)
        except Exception:
            logger.exception('Failed to write %d prediction rows', len(batch))

    async def flush_now(self) -> None:
        """Drain everything queued. Used by tests, which assert on rows rather
        than waiting on a timer."""
        await self._drain()


writer = PredictionWriter()
