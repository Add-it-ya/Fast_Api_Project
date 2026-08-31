from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.cache.redis_cache import get_redis
from app.db.session import engine

router = APIRouter()


@router.get('/health')
async def health():
    """Liveness - the process is up. No dependencies touched."""
    return {'status': 'ok'}


@router.get('/ready')
async def ready(request: Request, response: Response):
    """Readiness - the process can actually serve traffic."""
    checks = {'database': False, 'redis': False, 'model': False}

    try:
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
        checks['database'] = True
    except Exception:
        pass

    try:
        await get_redis().ping()
        checks['redis'] = True
    except Exception:
        pass

    checks['model'] = getattr(request.app.state, 'model', None) is not None

    ready_now = all(checks.values())
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {'ready': ready_now, 'checks': checks}
