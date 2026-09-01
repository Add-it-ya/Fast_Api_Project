import pytest

from app.core.config import settings


async def _seed(client, auth_headers, valid_car, flush_predictions, company, year, count):
    for i in range(count):
        payload = valid_car | {'company': company, 'year': year, 'km_driven': 1000 + i}
        await client.post('/predict', json=payload, headers=auth_headers)
    await flush_predictions()


async def test_history_returns_matching_rows(client, auth_headers, valid_car, flush_predictions):
    await _seed(client, auth_headers, valid_car, flush_predictions, 'Maruti', 2015, 3)

    response = await client.get(
        '/predictions/history', params={'company': 'Maruti', 'year': 2015}, headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body['company'] == 'Maruti'
    assert body['year'] == 2015
    assert body['count'] == 3
    assert len(body['items']) == 3


async def test_history_excludes_other_companies_and_years(client, auth_headers, valid_car, flush_predictions):
    await _seed(client, auth_headers, valid_car, flush_predictions, 'Maruti', 2015, 2)
    await _seed(client, auth_headers, valid_car, flush_predictions, 'BMW', 2015, 3)
    await _seed(client, auth_headers, valid_car, flush_predictions, 'Maruti', 2016, 4)

    response = await client.get(
        '/predictions/history', params={'company': 'Maruti', 'year': 2015}, headers=auth_headers
    )
    assert response.json()['count'] == 2


async def test_history_is_newest_first(client, auth_headers, valid_car, flush_predictions):
    await _seed(client, auth_headers, valid_car, flush_predictions, 'Tata', 2018, 5)

    response = await client.get(
        '/predictions/history', params={'company': 'Tata', 'year': 2018}, headers=auth_headers
    )
    timestamps = [item['created_at'] for item in response.json()['items']]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_history_paginates(client, auth_headers, valid_car, flush_predictions):
    await _seed(client, auth_headers, valid_car, flush_predictions, 'Honda', 2012, 6)

    first = await client.get(
        '/predictions/history',
        params={'company': 'Honda', 'year': 2012, 'limit': 2},
        headers=auth_headers,
    )
    second = await client.get(
        '/predictions/history',
        params={'company': 'Honda', 'year': 2012, 'limit': 2, 'offset': 2},
        headers=auth_headers,
    )

    assert first.json()['count'] == 2
    assert second.json()['count'] == 2
    first_ids = {item['id'] for item in first.json()['items']}
    second_ids = {item['id'] for item in second.json()['items']}
    assert first_ids.isdisjoint(second_ids)


async def test_history_is_empty_for_unseen_combination(client, auth_headers):
    response = await client.get(
        '/predictions/history', params={'company': 'Volvo', 'year': 1999}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()['count'] == 0


@pytest.mark.parametrize(
    'params',
    [
        {'company': 'Tesla', 'year': 2015},
        {'company': 'Maruti', 'year': 1800},
        {'company': 'Maruti', 'year': 2015, 'limit': 0},
        {'company': 'Maruti', 'year': 2015, 'limit': 5000},
        {'company': 'Maruti', 'year': 2015, 'offset': -1},
        {'year': 2015},
    ],
)
async def test_history_validates_query_parameters(client, auth_headers, params):
    response = await client.get('/predictions/history', params=params, headers=auth_headers)
    assert response.status_code == 422


async def test_history_requires_both_credentials(client, token):
    only_bearer = await client.get(
        '/predictions/history',
        params={'company': 'Maruti', 'year': 2015},
        headers={'Authorization': f'Bearer {token}'},
    )
    only_key = await client.get(
        '/predictions/history',
        params={'company': 'Maruti', 'year': 2015},
        headers={'api-key': settings.API_KEY},
    )
    assert only_bearer.status_code == 401
    assert only_key.status_code == 401
