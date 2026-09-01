from fastapi import APIRouter, Depends, Query, Request
from prometheus_client import Gauge

from app.core.dependencies import Principal, get_current_principal, get_prediction_repository
from app.core.exceptions import ModelUnavailableError
from app.db.repositories import PredictionRepository
from app.schemas.model import DriftReport, ModelInfo, ModelPerformance
from app.services.drift import monitor

router = APIRouter()

LIVE_MAE = Gauge(
    'model_live_mae',
    'Mean absolute error over predictions with a reported outcome',
    multiprocess_mode='max',
)
LIVE_SCORED = Gauge(
    'model_live_scored_predictions',
    'Predictions that have a reported outcome',
    multiprocess_mode='max',
)


@router.get('/model/info', response_model=ModelInfo)
async def model_info(request: Request):
    """Version, provenance and validation metrics of the served model."""
    bundle = getattr(request.app.state, 'model_bundle', None)
    if bundle is None:
        raise ModelUnavailableError()

    summary = bundle.summary()
    summary['version_match'] = (
        summary['sklearn_version'] is None or summary['sklearn_version'] == summary['sklearn_version_runtime']
    )
    return summary


@router.get('/model/drift', response_model=DriftReport)
async def model_drift(recompute: bool = Query(default=False)):
    """Population Stability Index of live features against training.

    Under 0.1 is stable, 0.1-0.25 a moderate shift, above 0.25 significant.
    """
    return monitor.refresh() if recompute else monitor.snapshot()


@router.get('/model/performance', response_model=ModelPerformance)
async def model_performance(
    request: Request,
    window: int = Query(default=1000, ge=1, le=100_000),
    _: Principal = Depends(get_current_principal),
    predictions: PredictionRepository = Depends(get_prediction_repository),
):
    """The model scored against reported outcomes, next to what it trained at."""
    live = await predictions.live_performance(limit=window)

    bundle = getattr(request.app.state, 'model_bundle', None)
    training = (bundle.metadata.get('metrics', {}) if bundle else {}) or {}

    if live['mae'] is not None:
        LIVE_MAE.set(live['mae'])
    LIVE_SCORED.set(live['scored'])

    if live['scored'] == 0:
        note = 'No outcomes reported yet. POST /predictions/{id}/actual to score a prediction.'
    else:
        note = (
            f'Live error over the last {live["scored"]} scored predictions, '
            'against held-out metrics from training.'
        )

    return ModelPerformance(
        scored=live['scored'],
        live_mae=live['mae'],
        live_mape_pct=live['mape_pct'],
        training_mae=training.get('mae'),
        training_mape_pct=training.get('mape_pct'),
        note=note,
    )
