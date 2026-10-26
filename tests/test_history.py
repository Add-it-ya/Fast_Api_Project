import pytest

from app.core.config import settings


async def _login_as(client, username: str) -> dict:
    credentials = {'username': username, 'password': 'test-password-1'}
    await client.post('/register', json=credentials)
    token = (await client.post('/login', json=credentials)).json()['access_token']
    return {'Authorization': f'Bearer {token}'}


async def _seed(client, headers, valid_car, flush_predictions, count, **overrides) -> list[str]:
    ids = []
    for i in range(count):
        payload = valid_car | {'km_driven': 1000 + i} | overrides
        ids.append((await client.post('/predict', json=payload, headers=headers)).json()['prediction_id'])
    await flush_predictions()
    return ids


async def test_history_returns_the_callers_predictions(client, auth_headers, valid_car, flush_predictions):
    made = await _seed(client, auth_headers, valid_car, flush_predictions, 3)

    response = await client.get('/predictions/history', headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body['count'] == 3
    assert {item['prediction_id'] for item in body['items']} == set(made)


async def test_history_does_not_show_other_users_predictions(client, valid_car, flush_predictions):
    alice = await _login_as(client, 'alice')
    bob = await _login_as(client, 'bob')
    alices = await _seed(client, alice, valid_car, flush_predictions, 2)
    await _seed(client, bob, valid_car, flush_predictions, 3)

    body = (await client.get('/predictions/history', headers=alice)).json()

    assert body['count'] == 2
    assert {item['prediction_id'] for item in body['items']} == set(alices)


async def test_history_can_be_narrowed_by_company_and_year(
    client, auth_headers, valid_car, flush_predictions
):
    await _seed(client, auth_headers, valid_car, flush_predictions, 2, company='Maruti', year=2015)
    await _seed(client, auth_headers, valid_car, flush_predictions, 3, company='BMW', year=2015)
    await _seed(client, auth_headers, valid_car, flush_predictions, 4, company='Maruti', year=2016)

    def count(params):
        return client.get('/predictions/history', params=params, headers=auth_headers)

    assert (await count({'company': 'Maruti', 'year': 2015})).json()['count'] == 2
    assert (await count({'company': 'Maruti'})).json()['count'] == 6
    assert (await count({'year': 2015})).json()['count'] == 5


async def test_history_is_newest_first(client, auth_headers, valid_car, flush_predictions):
    await _seed(client, auth_headers, valid_car, flush_predictions, 5)

    response = await client.get('/predictions/history', headers=auth_headers)
    timestamps = [item['created_at'] for item in response.json()['items']]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_history_paginates(client, auth_headers, valid_car, flush_predictions):
    await _seed(client, auth_headers, valid_car, flush_predictions, 6)

    first = await client.get('/predictions/history', params={'limit': 2}, headers=auth_headers)
    second = await client.get('/predictions/history', params={'limit': 2, 'offset': 2}, headers=auth_headers)

    assert first.json()['count'] == 2
    assert second.json()['count'] == 2
    first_ids = {item['prediction_id'] for item in first.json()['items']}
    second_ids = {item['prediction_id'] for item in second.json()['items']}
    assert first_ids.isdisjoint(second_ids)


async def test_history_is_empty_before_any_prediction(client, auth_headers):
    response = await client.get('/predictions/history', headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {'count': 0, 'items': []}


@pytest.mark.parametrize(
    'params',
    [
        {'company': 'Tesla'},
        {'year': 1800},
        {'limit': 0},
        {'limit': 5000},
        {'offset': -1},
    ],
)
async def test_history_validates_query_parameters(client, auth_headers, params):
    response = await client.get('/predictions/history', params=params, headers=auth_headers)
    assert response.status_code == 422


async def test_history_needs_a_user_token(client):
    no_token = await client.get('/predictions/history')
    only_key = await client.get('/predictions/history', headers={'api-key': settings.API_KEY})
    assert no_token.status_code == 401
    assert only_key.status_code == 401
