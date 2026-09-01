"""Shared test fixtures.

Environment variables are set before importing the app, because
app.core.config builds Settings at import time and refuses placeholders.
Tests get their own database and their own Redis logical db so a run never
touches development data.
"""

import os
import re
from urllib.parse import urlparse

_db = os.environ.get('DATABASE_URL', 'postgresql+asyncpg://carprice:carprice@localhost:5432/carprice')
os.environ['DATABASE_URL'] = _db.rsplit('/', 1)[0] + '/carprice_test'

_redis = re.sub(r'/\d+$', '', os.environ.get('REDIS_URL', 'redis://localhost:6379'))
os.environ['REDIS_URL'] = f'{_redis}/1'

os.environ.setdefault('API_KEY', 'test-api-key-value')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key-of-at-least-32-chars')

# Tests run in one process, so metrics use the default registry. The
# multiprocess directory is created by the container entrypoint, which pytest
# does not go through - leaving the variable set makes prometheus_client look
# for files nobody created.
os.environ.pop('PROMETHEUS_MULTIPROC_DIR', None)

# Readable tracebacks beat structured logs when a test fails.
os.environ.setdefault('LOG_FORMAT', 'text')

import asyncio

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.cache.redis_cache import close_redis, get_redis
from app.core.config import settings
from app.db.models import Base
from app.db.session import engine
from app.main import app
from app.services.drift import monitor
from app.services.model_registry import load_bundle
from app.services.prediction_writer import writer

VALID_CAR = {
    'company': 'Maruti',
    'year': 2015,
    'owner': 'Second',
    'fuel': 'Petrol',
    'seller_type': 'Individual',
    'transmission': 'Automatic',
    'km_driven': 200000,
    'mileage_mpg': 55,
    'engine_cc': 1250,
    'max_power_bhp': 80,
    'torque_nm': 200,
    'seats': 5,
}


async def _create_test_database() -> None:
    parsed = urlparse(settings.DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://'))
    name = parsed.path.lstrip('/')
    admin = await asyncpg.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port,
        database='postgres',
    )
    try:
        if not await admin.fetchval('SELECT 1 FROM pg_database WHERE datname = $1', name):
            await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


async def _create_schema() -> None:
    setup_engine = create_async_engine(settings.DATABASE_URL)
    async with setup_engine.begin() as conn:
        # Drop first: create_all leaves an existing table alone, so a column
        # added since the test database was last built would never appear.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await setup_engine.dispose()


@pytest.fixture(scope='session')
def event_loop():
    # One loop for the whole session so the pooled asyncpg connections in
    # app.db.session stay bound to a single running loop.
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope='session', autouse=True)
def prepare_database(event_loop):
    event_loop.run_until_complete(_create_test_database())
    event_loop.run_until_complete(_create_schema())
    yield
    event_loop.run_until_complete(engine.dispose())
    event_loop.run_until_complete(close_redis())


@pytest.fixture(autouse=True)
async def reset_state():
    yield
    # Drain the writer first, or rows queued by this test land in the next one.
    await writer.flush_now()
    async with engine.begin() as conn:
        await conn.execute(text('TRUNCATE predictions, users RESTART IDENTITY CASCADE'))
    await get_redis().flushdb()


@pytest.fixture
def flush_predictions():
    """Predictions are written by a batching background consumer, so a test
    that asserts on rows has to drain the queue first."""

    async def _flush():
        await writer.flush_now()

    return _flush


@pytest.fixture(scope='session')
def model_bundle():
    return load_bundle(settings.MODEL_PATH, settings.MODEL_METADATA_PATH)


@pytest.fixture
async def client(model_bundle):
    app.state.model_bundle = model_bundle
    app.state.model = model_bundle.pipeline
    # Normally done in the lifespan handler, which the ASGI transport skips.
    monitor.configure(model_bundle.training_distribution)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as ac:
        yield ac


@pytest.fixture
async def registered_user(client):
    credentials = {'username': 'tester', 'password': 'test-password-1'}
    await client.post('/register', json=credentials)
    return credentials


@pytest.fixture
async def token(client, registered_user):
    response = await client.post('/login', json=registered_user)
    return response.json()['access_token']


@pytest.fixture
def auth_headers(token):
    return {'Authorization': f'Bearer {token}', 'api-key': settings.API_KEY}


@pytest.fixture
def valid_car():
    return dict(VALID_CAR)
