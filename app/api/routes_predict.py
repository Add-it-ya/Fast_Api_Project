from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_user, get_prediction_service, require_api_key
from app.db.models import User
from app.schemas.prediction import CarFeatures, PredictionResponse
from app.services.model_service import PredictionService

router = APIRouter()


@router.post('/predict', response_model=PredictionResponse, dependencies=[Depends(require_api_key)])
async def predict_price(
    car: CarFeatures,
    user: User = Depends(get_current_user),
    service: PredictionService = Depends(get_prediction_service),
):
    price, cache_hit = await service.predict(car.model_dump(), user_id=user.id)
    return PredictionResponse(predicted_price=f'{price:,.2f}', cached=cache_hit)
