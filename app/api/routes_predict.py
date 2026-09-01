import time

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    Principal,
    get_current_principal,
    get_prediction_repository,
    get_prediction_service,
    require_api_key,
)
from app.core.exceptions import AppError
from app.core.metrics import PREDICTION_LATENCY, PREDICTION_VALUE, PREDICTIONS
from app.core.rate_limit import enforce_rate_limit
from app.db.repositories import PredictionRepository
from app.schemas.model import ActualPrice, ScoredPrediction
from app.schemas.prediction import (
    CarFeatures,
    Company,
    PredictionHistory,
    PredictionRecord,
    PredictionResponse,
)
from app.services.drift import monitor
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
    started = time.perf_counter()
    features = car.model_dump()
    price, cache_hit = await service.predict(features)

    outcome = 'hit' if cache_hit else 'miss'
    PREDICTION_LATENCY.labels(cache=outcome).observe(time.perf_counter() - started)
    PREDICTIONS.labels(company=features['company'], cache=outcome).inc()
    PREDICTION_VALUE.observe(price)

    monitor.observe(features)

    writer.enqueue({**features, 'user_id': principal.id, 'predicted_price': price, 'cache_hit': cache_hit})
    return PredictionResponse(predicted_price=f'{price:,.2f}', cached=cache_hit)


@router.get(
    '/predictions/history',
    response_model=PredictionHistory,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
async def prediction_history(
    company: Company,
    year: int = Query(ge=1980, le=2026),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: Principal = Depends(get_current_principal),
    predictions: PredictionRepository = Depends(get_prediction_repository),
):
    rows = await predictions.history(company=company, year=year, limit=limit, offset=offset)
    return PredictionHistory(
        company=company,
        year=year,
        count=len(rows),
        items=[PredictionRecord.model_validate(row) for row in rows],
    )


class PredictionNotFoundError(AppError):
    status_code = 404
    detail = 'No such prediction'


@router.post(
    '/predictions/{prediction_id}/actual',
    response_model=ScoredPrediction,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
async def record_actual_price(
    prediction_id: int,
    body: ActualPrice,
    _: Principal = Depends(get_current_principal),
    predictions: PredictionRepository = Depends(get_prediction_repository),
):
    """Report the real sale price so the prediction can be scored."""
    row = await predictions.record_actual(prediction_id, body.actual_price)
    if row is None:
        raise PredictionNotFoundError()

    predicted = float(row.predicted_price)
    return ScoredPrediction(
        id=row.id,
        predicted_price=predicted,
        actual_price=body.actual_price,
        absolute_error=round(abs(predicted - body.actual_price), 2),
    )
