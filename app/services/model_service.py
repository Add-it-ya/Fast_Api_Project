"""Business logic for price prediction."""

import asyncio
import logging
import time

import pandas as pd

from app.cache.redis_cache import build_cache_key, get_cached_prediction, set_cached_prediction
from app.core.exceptions import ModelUnavailableError
from app.core.metrics import INFERENCE_LATENCY

logger = logging.getLogger(__name__)

FEATURE_ORDER = [
    'company',
    'year',
    'owner',
    'fuel',
    'seller_type',
    'transmission',
    'km_driven',
    'mileage_mpg',
    'engine_cc',
    'max_power_bhp',
    'torque_nm',
    'seats',
]


def _run_inference(model, features: dict) -> float:
    frame = pd.DataFrame([features], columns=FEATURE_ORDER)
    return float(model.predict(frame)[0])


class PredictionService:
    def __init__(self, model):
        self._model = model

    async def predict(self, features: dict) -> tuple[float, bool]:
        if self._model is None:
            raise ModelUnavailableError()

        cache_key = build_cache_key(features)
        cached = await get_cached_prediction(cache_key)

        if cached is None:
            # scikit-learn inference is synchronous and CPU-bound; running it
            # inline would block the event loop and serialise every other
            # request in flight.
            started = time.perf_counter()
            price = await asyncio.to_thread(_run_inference, self._model, features)
            INFERENCE_LATENCY.observe(time.perf_counter() - started)

            await set_cached_prediction(cache_key, price)
            cache_hit = False
        else:
            price = cached
            cache_hit = True

        return price, cache_hit
