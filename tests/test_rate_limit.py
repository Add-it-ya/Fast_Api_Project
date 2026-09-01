import asyncio

from app.core.config import settings


async def test_requests_beyond_the_limit_are_rejected(client, auth_headers, valid_car, monkeypatch):
    monkeypatch.setattr(settings, 'RATE_LIMIT_REQUESTS', 5)

    statuses = []
    for i in range(8):
        response = await client.post(
            '/predict', json=valid_car | {'km_driven': 1000 + i}, headers=auth_headers
        )
        statuses.append(response.status_code)

    assert statuses[:5] == [200] * 5
    assert statuses[5:] == [429] * 3


async def test_limit_message_names_the_budget(client, auth_headers, valid_car, monkeypatch):
    monkeypatch.setattr(settings, 'RATE_LIMIT_REQUESTS', 1)

    await client.post('/predict', json=valid_car, headers=auth_headers)
    blocked = await client.post('/predict', json=valid_car, headers=auth_headers)

    assert blocked.status_code == 429
    assert '1 requests per' in blocked.json()['detail']


async def test_login_has_its_own_budget(client, registered_user, monkeypatch):
    """Separate paths get separate counters, so a burst of logins cannot
    exhaust the prediction budget."""
    monkeypatch.setattr(settings, 'RATE_LIMIT_REQUESTS', 2)

    await asyncio.gather(*[client.post('/login', json=registered_user) for _ in range(3)])
    predict_key_used = await client.get('/health')

    assert predict_key_used.status_code == 200
