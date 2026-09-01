from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine


async def test_predict_returns_a_price(client, auth_headers, valid_car):
    response = await client.post('/predict', json=valid_car, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body['cached'] is False
    assert float(body['predicted_price'].replace(',', '')) > 0


async def test_second_identical_request_is_served_from_cache(client, auth_headers, valid_car):
    first = await client.post('/predict', json=valid_car, headers=auth_headers)
    second = await client.post('/predict', json=valid_car, headers=auth_headers)

    assert first.json()['cached'] is False
    assert second.json()['cached'] is True
    assert first.json()['predicted_price'] == second.json()['predicted_price']


async def test_prediction_is_logged_to_the_database(client, auth_headers, valid_car, flush_predictions):
    await client.post('/predict', json=valid_car, headers=auth_headers)
    await flush_predictions()

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text('SELECT company, year, predicted_price, cache_hit, user_id FROM predictions')
            )
        ).one()

    assert row.company == 'Maruti'
    assert row.year == 2015
    assert float(row.predicted_price) > 0
    assert row.cache_hit is False
    assert row.user_id is not None


async def test_cache_hits_are_recorded_separately(client, auth_headers, valid_car, flush_predictions):
    await client.post('/predict', json=valid_car, headers=auth_headers)
    await client.post('/predict', json=valid_car, headers=auth_headers)
    await flush_predictions()

    async with engine.connect() as conn:
        hits = await conn.scalar(text('SELECT count(*) FROM predictions WHERE cache_hit'))
        total = await conn.scalar(text('SELECT count(*) FROM predictions'))

    assert total == 2
    assert hits == 1


async def test_missing_api_key_is_unauthorised_not_unprocessable(client, token, valid_car):
    response = await client.post('/predict', json=valid_car, headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401
    assert response.json()['detail'] == 'Missing or invalid API key'


async def test_wrong_api_key_is_rejected(client, token, valid_car):
    response = await client.post(
        '/predict',
        json=valid_car,
        headers={'Authorization': f'Bearer {token}', 'api-key': 'not-the-key'},
    )
    assert response.status_code == 401


async def test_missing_bearer_token_is_rejected(client, valid_car):
    response = await client.post('/predict', json=valid_car, headers={'api-key': settings.API_KEY})
    assert response.status_code == 401


async def test_malformed_bearer_token_is_rejected(client, valid_car):
    response = await client.post(
        '/predict',
        json=valid_car,
        headers={'Authorization': 'Bearer not.a.jwt', 'api-key': settings.API_KEY},
    )
    assert response.status_code == 401
    assert response.json()['detail'] == 'Invalid or expired token'
