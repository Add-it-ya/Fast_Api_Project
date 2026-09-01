import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import routes_auth, routes_health, routes_model, routes_predict
from app.cache.redis_cache import close_redis
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import configure_logging, shutdown_logging
from app.db.session import engine
from app.middleware.logging_middleware import LoggingMiddleware
from app.services.drift import monitor
from app.services.model_registry import load_bundle
from app.services.prediction_writer import writer

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load once at startup rather than on import, so a broken artifact
    # surfaces as a 503 on /predict instead of an import-time crash.
    try:
        bundle = load_bundle(settings.MODEL_PATH, settings.MODEL_METADATA_PATH)
        app.state.model_bundle = bundle
        app.state.model = bundle.pipeline
        monitor.configure(bundle.training_distribution)
        logger.info(
            'Loaded model v%s from %s (drift reference: %s)',
            bundle.version,
            settings.MODEL_PATH,
            'configured' if monitor.configured else 'unavailable',
        )
    except Exception:
        app.state.model_bundle = None
        app.state.model = None
        logger.exception('Failed to load model from %s', settings.MODEL_PATH)

    writer.start()

    yield

    await writer.stop()
    await close_redis()
    await engine.dispose()
    shutdown_logging()


app = FastAPI(title=settings.PROJECT_NAME, version='2.0.0', lifespan=lifespan)

app.add_middleware(LoggingMiddleware)

app.include_router(routes_health.router, tags=['Health'])
app.include_router(routes_auth.router, tags=['Auth'])
app.include_router(routes_predict.router, tags=['Prediction'])
app.include_router(routes_model.router, tags=['Model'])

Instrumentator().instrument(app).expose(app)

register_exception_handlers(app)
