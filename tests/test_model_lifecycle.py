import sklearn

from app.services.model_registry import load_bundle


async def test_model_info_reports_version_and_provenance(client):
    response = await client.get('/model/info')
    assert response.status_code == 200
    body = response.json()

    assert body['version'] >= 1
    assert body['model_type']
    assert body['trained_at']
    assert body['data_sha256']
    assert body['training_rows'] > 0


async def test_model_info_flags_sklearn_version_agreement(client):
    body = (await client.get('/model/info')).json()

    assert body['sklearn_version_runtime'] == sklearn.__version__
    # The artifact is a pickle; loading it under a different scikit-learn than
    # it was written with raises rather than degrading, so the mismatch has to
    # be visible.
    assert body['version_match'] is (body['sklearn_version'] == sklearn.__version__)


async def test_model_info_carries_metrics_and_a_baseline(client):
    metrics = (await client.get('/model/info')).json()
    assert metrics['metrics']['mae'] > 0
    assert metrics['metrics']['r2'] <= 1.0
    # A baseline that predicts the training mean, so r2 means something.
    assert metrics['baseline_metrics']['mae'] > metrics['metrics']['mae']


async def test_model_info_does_not_leak_the_training_distribution(client):
    """Useful for drift internally, but bulky and closer to the training data
    than an API consumer should get."""
    assert 'training_distribution' not in (await client.get('/model/info')).json()


def test_bundle_loads_without_metadata(tmp_path):
    from app.core.config import settings

    bundle = load_bundle(settings.MODEL_PATH, str(tmp_path / 'missing.json'))
    assert bundle.pipeline is not None
    assert bundle.version is None
    assert bundle.training_distribution == {}


def test_bundle_survives_corrupt_metadata(tmp_path):
    from app.core.config import settings

    broken = tmp_path / 'broken.json'
    broken.write_text('{not json')

    bundle = load_bundle(settings.MODEL_PATH, str(broken))
    assert bundle.pipeline is not None
    assert bundle.version is None


async def test_recording_an_actual_price_scores_the_prediction(
    client, auth_headers, operator_headers, valid_car, flush_predictions
):
    await client.post('/predict', json=valid_car, headers=auth_headers)
    await flush_predictions()

    history = await client.get(
        '/predictions/history',
        params={'company': valid_car['company'], 'year': valid_car['year']},
        headers=auth_headers,
    )
    prediction_id = history.json()['items'][0]['id']

    response = await client.post(
        f'/predictions/{prediction_id}/actual',
        json={'actual_price': 500000},
        headers=operator_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body['id'] == prediction_id
    assert body['actual_price'] == 500000
    assert body['absolute_error'] == round(abs(body['predicted_price'] - 500000), 2)


async def test_recording_against_a_missing_prediction_is_404(client, operator_headers):
    response = await client.post(
        '/predictions/999999999/actual', json={'actual_price': 100}, headers=operator_headers
    )
    assert response.status_code == 404


async def test_actual_price_is_validated(client, operator_headers):
    response = await client.post('/predictions/1/actual', json={'actual_price': -5}, headers=operator_headers)
    assert response.status_code == 422


async def test_a_user_token_cannot_report_a_sale_price(client, auth_headers):
    response = await client.post('/predictions/1/actual', json={'actual_price': 100}, headers=auth_headers)
    assert response.status_code == 401
    assert response.json()['detail'] == 'Missing or invalid API key'


async def test_a_wrong_operator_key_is_rejected(client):
    response = await client.post(
        '/predictions/1/actual', json={'actual_price': 100}, headers={'api-key': 'not-the-key'}
    )
    assert response.status_code == 401


async def test_a_non_ascii_api_key_is_rejected_not_crashed(client):
    # Header values arrive decoded as latin-1, so any byte over 0x7f becomes a
    # non-ASCII character - which secrets.compare_digest refuses for str.
    response = await client.post(
        '/predictions/1/actual', json={'actual_price': 100}, headers={'api-key': 'clé-invalide'.encode()}
    )
    assert response.status_code == 401


async def test_reading_drift_is_open_but_recomputing_needs_the_key(client, operator_headers):
    assert (await client.get('/model/drift')).status_code == 200
    assert (await client.get('/model/drift', params={'recompute': 'true'})).status_code == 401
    forced = await client.get('/model/drift', params={'recompute': 'true'}, headers=operator_headers)
    assert forced.status_code == 200


async def test_performance_is_empty_before_any_outcome(client, auth_headers):
    response = await client.get('/model/performance', headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body['scored'] == 0
    assert body['live_mae'] is None
    assert 'POST /predictions/{id}/actual' in body['note']


async def test_performance_reports_live_error_against_training(
    client, auth_headers, operator_headers, valid_car, flush_predictions
):
    await client.post('/predict', json=valid_car, headers=auth_headers)
    await flush_predictions()

    history = await client.get(
        '/predictions/history',
        params={'company': valid_car['company'], 'year': valid_car['year']},
        headers=auth_headers,
    )
    item = history.json()['items'][0]
    await client.post(
        f'/predictions/{item["id"]}/actual',
        json={'actual_price': 500000},
        headers=operator_headers,
    )

    body = (await client.get('/model/performance', headers=auth_headers)).json()
    assert body['scored'] == 1
    assert body['live_mae'] == round(abs(float(item['predicted_price']) - 500000), 2)
    assert body['training_mae'] > 0
