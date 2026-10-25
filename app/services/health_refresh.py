"""Keeps the model-health gauges current.

Prometheus only sees the value a gauge was last set to. Drift and live error
are both computed from the prediction log, so something has to recompute them
on a timer, or the dashboard and the drift alert would only move when somebody
called an endpoint. Each worker runs one of these; they all read the same rows,
so the gauges agree.
"""

import asyncio
import contextlib
import logging

from prometheus_client import Gauge

from app.db.repositories import PredictionRepository
from app.db.session import SessionFactory
from app.services.drift import monitor

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 60.0
# Same window /model/performance uses by default.
LIVE_WINDOW = 1000

LIVE_MAE = Gauge(
    'model_live_mae',
    'Mean absolute error over the newest predictions with a reported outcome',
    multiprocess_mode='max',
)
LIVE_SCORED = Gauge(
    'model_live_scored_predictions',
    'Predictions that have a reported outcome',
    multiprocess_mode='max',
)


async def refresh_live_error() -> None:
    async with SessionFactory() as session:
        live = await PredictionRepository(session).live_performance(limit=LIVE_WINDOW)
    if live['mae'] is not None:
        LIVE_MAE.set(live['mae'])
    LIVE_SCORED.set(live['scored'])


class HealthRefresher:
    def __init__(self, interval_seconds: float = INTERVAL_SECONDS):
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name='health-refresh')

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def refresh_once(self) -> None:
        # Each check fails on its own and keeps its last values; the loop must
        # outlive a database blip.
        try:
            await monitor.refresh()
        except Exception:
            logger.exception('Drift refresh failed')
        try:
            await refresh_live_error()
        except Exception:
            logger.exception('Live error refresh failed')

    async def _run(self) -> None:
        while True:
            await self.refresh_once()
            await asyncio.sleep(self._interval)


refresher = HealthRefresher()
