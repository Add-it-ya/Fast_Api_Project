from fastapi import APIRouter, Depends

from app.core.dependencies import (
    Principal,
    get_current_principal,
    get_prediction_service,
    require_api_key,
)
from app.core.rate_limit import enforce_rate_limit
from app.schemas.prediction import CarFeatures, PredictionResponse
from app.services.model_service import PredictionService
from app.services.prediction_writer import writer

router = APIRouter()


@router.post(
    '/predict',
    response_model=PredictionResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
async def predict_price(
    car: CarFeatures,
    principal: Principal = Depends(get_current_principal),
    service: PredictionService = Depends(get_prediction_service),
):
    features = car.model_dump()
    price, cache_hit = await service.predict(features)

    writer.enqueue({**features, 'user_id': principal.id, 'predicted_price': price, 'cache_hit': cache_hit})
    return PredictionResponse(predicted_price=f'{price:,.2f}', cached=cache_hit)
