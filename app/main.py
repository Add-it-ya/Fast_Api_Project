import logging
from contextlib import asynccontextmanager

import joblib
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import routes_auth, routes_health, routes_predict
from app.cache.redis_cache import close_redis
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.db.session import engine
from app.middleware.logging_middleware import LoggingMiddleware

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load once at startup rather than on import, so a broken artifact
    # surfaces as a 503 on /predict instead of an import-time crash.
    try:
        app.state.model = joblib.load(settings.MODEL_PATH)
        logger.info('Loaded model from %s', settings.MODEL_PATH)
    except Exception:
        app.state.model = None
        logger.exception('Failed to load model from %s', settings.MODEL_PATH)

    yield

    await close_redis()
    await engine.dispose()


app = FastAPI(title=settings.PROJECT_NAME, version='2.0.0', lifespan=lifespan)

app.add_middleware(LoggingMiddleware)

app.include_router(routes_health.router, tags=['Health'])
app.include_router(routes_auth.router, tags=['Auth'])
app.include_router(routes_predict.router, tags=['Prediction'])

Instrumentator().instrument(app).expose(app)

register_exception_handlers(app)
