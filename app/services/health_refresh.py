"""Keeps the model-health gauges current.

Prometheus only sees the value a gauge was last set to. Drift is computed from
the prediction log, so something has to recompute it on a timer, or the
dashboard and the drift alert would only move when somebody called the
endpoint. Each worker runs one of these; they all read the same rows, so the
gauges agree.
"""

import asyncio
import contextlib
import logging

from app.services.drift import monitor

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 60.0


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
        # A failed refresh keeps the last values; the loop must outlive a
        # database blip.
        try:
            await monitor.refresh()
        except Exception:
            logger.exception('Drift refresh failed')

    async def _run(self) -> None:
        while True:
            await self.refresh_once()
            await asyncio.sleep(self._interval)


refresher = HealthRefresher()
