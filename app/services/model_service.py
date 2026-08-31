"""Business logic for price prediction."""

import asyncio
import logging

import pandas as pd

from app.cache.redis_cache import build_cache_key, get_cached_prediction, set_cached_prediction
from app.core.exceptions import ModelUnavailableError
from app.db.repositories import PredictionRepository

logger = logging.getLogger(__name__)

FEATURE_ORDER = [
    'company', 'year', 'owner', 'fuel', 'seller_type', 'transmission',
    'km_driven', 'mileage_mpg', 'engine_cc', 'max_power_bhp', 'torque_nm', 'seats',
]


def _run_inference(model, features: dict) -> float:
    frame = pd.DataFrame([features], columns=FEATURE_ORDER)
    return float(model.predict(frame)[0])


class PredictionService:
    def __init__(self, model, predictions: PredictionRepository):
        self._model = model
        self._predictions = predictions

    async def predict(self, features: dict, user_id: int | None) -> tuple[float, bool]:
        if self._model is None:
            raise ModelUnavailableError()

        cache_key = build_cache_key(features)
        price = await get_cached_prediction(cache_key)
        cache_hit = price is not None

        if not cache_hit:
            # scikit-learn inference is synchronous and CPU-bound; running it
            # inline would block the event loop and serialise every other
            # request in flight.
            price = await asyncio.to_thread(_run_inference, self._model, features)
            await set_cached_prediction(cache_key, price)

        await self._predictions.log(
            user_id=user_id,
            features=features,
            predicted_price=price,
            cache_hit=cache_hit,
        )
        return price, cache_hit
