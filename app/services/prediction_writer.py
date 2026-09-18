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

A multi-row INSERT is all or nothing, so one bad row - a prediction logged for
a user deleted after their token was issued, which breaks the foreign key -
would take its whole batch down with it. When the database rejects a batch it
is retried row by row, and only the rows that fail again are lost. Every lost
row is counted, whatever the reason, so the shedding alert sees all of them.
"""

import asyncio
import contextlib
import logging

from prometheus_client import Counter, Gauge
from sqlalchemy.exc import IntegrityError

from app.db.repositories import PredictionRepository
from app.db.session import SessionFactory

logger = logging.getLogger(__name__)

ROWS_WRITTEN = Counter('prediction_log_rows_written_total', 'Prediction rows persisted')
ROWS_DROPPED = Counter(
    'prediction_log_rows_dropped_total',
    'Prediction rows lost before reaching the database',
    # queue_full: shed to protect the request path. rejected: the database
    # refused the row itself. write_failed: the database could not be reached.
    ['reason'],
)
QUEUE_DEPTH = Gauge('prediction_log_queue_depth', 'Rows waiting to be written', multiprocess_mode='livesum')


class PredictionWriter:
    def __init__(self, batch_size: int = 200, max_queue: int = 20_000):
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None
        self._batch_size = batch_size
        self.dropped = 0
        self.written = 0
        self._shed = 0

    def enqueue(self, row: dict) -> None:
        """Never blocks and never raises. Shedding a log row is preferable to
        slowing down or failing the request that produced it."""
        try:
            self._queue.put_nowait(row)
            QUEUE_DEPTH.set(self._queue.qsize())
        except asyncio.QueueFull:
            self._drop(1, 'queue_full')
            self._shed += 1
            if self._shed % 1000 == 1:
                logger.warning('Prediction log queue full, shed %d rows so far', self._shed)

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
            self._wrote(len(batch))
        except IntegrityError:
            # Something in the batch broke a constraint. That is a property of
            # a row, not of the database, so the other rows can still land.
            await self._flush_row_by_row(batch)
        except Exception:
            # The database itself is the problem. Retrying each row would only
            # repeat the same failure once per row.
            logger.exception('Failed to write %d prediction rows', len(batch))
            self._drop(len(batch), 'write_failed')
        QUEUE_DEPTH.set(self._queue.qsize())

    async def _flush_row_by_row(self, batch: list[dict]) -> None:
        written = rejected = 0
        try:
            async with SessionFactory() as session:
                repository = PredictionRepository(session)
                for row in batch:
                    try:
                        await repository.log_many([row])
                        written += 1
                    except IntegrityError:
                        await session.rollback()
                        rejected += 1
        except Exception:
            logger.exception('Row-by-row retry of %d prediction rows failed', len(batch))
            self._drop(len(batch) - written - rejected, 'write_failed')

        self._wrote(written)
        if rejected:
            logger.warning(
                'Database rejected %d of %d prediction rows; wrote the rest individually',
                rejected,
                len(batch),
            )
            self._drop(rejected, 'rejected')

    def _wrote(self, count: int) -> None:
        self.written += count
        ROWS_WRITTEN.inc(count)

    def _drop(self, count: int, reason: str) -> None:
        self.dropped += count
        ROWS_DROPPED.labels(reason=reason).inc(count)

    async def flush_now(self) -> None:
        """Drain everything queued. Used by tests, which assert on rows rather
        than waiting on a timer."""
        await self._drain()


writer = PredictionWriter()
