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
    client, auth_headers, valid_car, flush_predictions
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
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body['id'] == prediction_id
    assert body['actual_price'] == 500000
    assert body['absolute_error'] == round(abs(body['predicted_price'] - 500000), 2)


async def test_recording_against_a_missing_prediction_is_404(client, auth_headers):
    response = await client.post(
        '/predictions/999999999/actual', json={'actual_price': 100}, headers=auth_headers
    )
    assert response.status_code == 404


async def test_actual_price_is_validated(client, auth_headers):
    response = await client.post('/predictions/1/actual', json={'actual_price': -5}, headers=auth_headers)
    assert response.status_code == 422


async def test_performance_is_empty_before_any_outcome(client, auth_headers):
    response = await client.get('/model/performance', headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body['scored'] == 0
    assert body['live_mae'] is None
    assert 'POST /predictions/{id}/actual' in body['note']


async def test_performance_reports_live_error_against_training(
    client, auth_headers, valid_car, flush_predictions
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
        headers=auth_headers,
    )

    body = (await client.get('/model/performance', headers=auth_headers)).json()
    assert body['scored'] == 1
    assert body['live_mae'] == round(abs(float(item['predicted_price']) - 500000), 2)
    assert body['training_mae'] > 0
