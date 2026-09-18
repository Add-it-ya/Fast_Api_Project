import pytest

from app.core.config import settings


@pytest.fixture
def limit(monkeypatch):
    """Set the per-window budget for one test.

    The window is stretched far past any test's runtime, so a run that happens
    to straddle a window boundary cannot reset the counter halfway through.
    """
    monkeypatch.setattr(settings, 'RATE_LIMIT_WINDOW_SECONDS', 86_400)

    def _set(requests: int) -> None:
        monkeypatch.setattr(settings, 'RATE_LIMIT_REQUESTS', requests)

    return _set


async def _login_as(client, username: str) -> dict:
    credentials = {'username': username, 'password': 'test-password-1'}
    await client.post('/register', json=credentials)
    token = (await client.post('/login', json=credentials)).json()['access_token']
    return {'Authorization': f'Bearer {token}', 'api-key': settings.API_KEY}


async def test_requests_beyond_the_limit_are_rejected(client, auth_headers, valid_car, limit):
    limit(5)

    statuses = []
    for i in range(8):
        response = await client.post(
            '/predict', json=valid_car | {'km_driven': 1000 + i}, headers=auth_headers
        )
        statuses.append(response.status_code)

    assert statuses[:5] == [200] * 5
    assert statuses[5:] == [429] * 3


async def test_limit_message_names_the_budget(client, auth_headers, valid_car, limit):
    limit(1)

    await client.post('/predict', json=valid_car, headers=auth_headers)
    blocked = await client.post('/predict', json=valid_car, headers=auth_headers)

    assert blocked.status_code == 429
    assert '1 requests per' in blocked.json()['detail']


async def test_a_new_api_key_header_does_not_buy_a_new_login_budget(client, registered_user, limit):
    """/login never checks the api-key header, so it must not decide which
    bucket a request is counted in - otherwise every guess gets a fresh one."""
    limit(5)

    statuses = [
        (await client.post('/login', json=registered_user, headers={'api-key': f'forged-{i}'})).status_code
        for i in range(8)
    ]

    assert statuses[:5] == [200] * 5
    assert statuses[5:] == [429] * 3


async def test_a_forged_token_does_not_buy_a_new_budget(client, registered_user, limit):
    limit(3)

    statuses = [
        (
            await client.post(
                '/login', json=registered_user, headers={'Authorization': f'Bearer forged.token.{i}'}
            )
        ).status_code
        for i in range(5)
    ]

    assert statuses == [200, 200, 200, 429, 429]


async def test_each_user_has_their_own_budget(client, valid_car, limit):
    """Everyone sends the same API key, so it cannot be the identity - one
    busy user would exhaust the budget of every other."""
    alice = await _login_as(client, 'alice')
    bob = await _login_as(client, 'bob')
    limit(2)

    for i in range(3):
        await client.post('/predict', json=valid_car | {'km_driven': 1000 + i}, headers=alice)
    alice_blocked = await client.post('/predict', json=valid_car, headers=alice)
    bob_allowed = await client.post('/predict', json=valid_car, headers=bob)

    assert alice_blocked.status_code == 429
    assert bob_allowed.status_code == 200


async def test_each_route_has_its_own_budget(client, auth_headers, valid_car, limit):
    limit(2)
    params = {'company': 'Maruti', 'year': 2015}

    history = [
        (await client.get('/predictions/history', params=params, headers=auth_headers)).status_code
        for _ in range(3)
    ]
    predict = await client.post('/predict', json=valid_car, headers=auth_headers)

    assert history == [200, 200, 429]
    assert predict.status_code == 200


async def test_path_parameters_share_one_budget(client, auth_headers, limit):
    """/predictions/1/actual and /predictions/2/actual are the same endpoint.
    Counting them apart would give a caller a fresh budget per id."""
    limit(2)

    statuses = [
        (
            await client.post(
                f'/predictions/{prediction_id}/actual', json={'actual_price': 500000}, headers=auth_headers
            )
        ).status_code
        for prediction_id in (1, 2, 3)
    ]

    # No predictions exist, so the first two are 404s - but they still count.
    assert statuses == [404, 404, 429]
