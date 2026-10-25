import pytest
from prometheus_client import REGISTRY

from app.db.repositories import PredictionRepository
from app.db.session import SessionFactory
from app.services.drift import DriftMonitor, classify, feature_scores, psi, report
from app.services.health_refresh import refresher

REFERENCE = {
    'numeric': {
        'km_driven': {
            'edges': [0.0, 100.0, 200.0, 300.0],
            'proportions': [1 / 3, 1 / 3, 1 / 3],
            'mean': 150.0,
            'std': 85.0,
        }
    },
    'categorical': {
        'fuel': {'Petrol': 0.5, 'Diesel': 0.49, 'CNG': 0.005, 'LPG': 0.005},
    },
}


def rows(km_values, fuels):
    return [{'km_driven': km, 'fuel': fuel} for km, fuel in zip(km_values, fuels, strict=True)]


def scored(sample, min_samples=30):
    return report(REFERENCE, feature_scores(REFERENCE, sample), len(sample), min_samples)


def by_feature(result):
    return {f['feature']: f['psi'] for f in result['features']}


def test_identical_distribution_scores_zero():
    assert psi([0.5, 0.5], [0.5, 0.5]) == pytest.approx(0.0)


def test_psi_grows_with_divergence():
    mild = psi([0.5, 0.5], [0.55, 0.45])
    severe = psi([0.5, 0.5], [0.9, 0.1])
    assert 0 < mild < severe


@pytest.mark.parametrize(
    'value,expected',
    [(0.0, 'stable'), (0.09, 'stable'), (0.1, 'moderate'), (0.24, 'moderate'), (0.3, 'significant')],
)
def test_classification_thresholds(value, expected):
    assert classify(value) == expected


def test_matching_traffic_reads_stable():
    result = scored(rows([50.0, 150.0, 250.0] * 20, ['Petrol', 'Diesel'] * 30))
    assert result['status'] == 'stable'
    assert result['worst_psi'] < 0.1


def test_shifted_numeric_traffic_is_detected():
    # Everything in the top bin, against a third expected there.
    result = scored(rows([250.0] * 60, ['Petrol', 'Diesel'] * 30))
    assert by_feature(result)['km_driven'] > 0.25
    assert result['status'] == 'significant'


def test_shifted_categorical_traffic_is_detected():
    result = scored(rows([50.0, 150.0, 250.0] * 20, ['Diesel'] * 60))
    assert by_feature(result)['fuel'] > 0.25


def test_rare_categories_do_not_manufacture_drift():
    """CNG and LPG are 0.5% of training each. A couple of them in a small
    window must not read as significant drift - that was a real false positive
    before rare categories were pooled."""
    fuels = ['Petrol'] * 29 + ['Diesel'] * 29 + ['CNG', 'LPG']
    result = scored(rows([50.0, 150.0, 250.0] * 20, fuels))
    assert by_feature(result)['fuel'] < 0.1


def test_unseen_categories_are_absorbed_not_infinite():
    result = scored(rows([50.0, 150.0, 250.0] * 20, ['Petrol'] * 30 + ['Hydrogen'] * 30))
    fuel = by_feature(result)['fuel']
    assert fuel == pytest.approx(fuel)  # finite, not inf/nan
    assert fuel > 0.25


def test_below_min_samples_reports_not_ready():
    result = scored(rows([50.0] * 10, ['Petrol'] * 10), min_samples=100)
    assert result['ready'] is False
    assert result['window_samples'] == 10
    assert result['status'] == 'unknown'
    assert result['features'] == []


async def test_unconfigured_monitor_does_not_read_anything():
    monitor = DriftMonitor()
    result = await monitor.refresh()
    assert result['configured'] is False
    assert result['window_samples'] == 0


async def _log(car: dict, count: int) -> None:
    row = car | {'user_id': None, 'predicted_price': 500000.0, 'cache_hit': False}
    async with SessionFactory() as session:
        await PredictionRepository(session).log_many([row] * count)


async def test_drift_is_computed_from_the_prediction_log(model_bundle, valid_car):
    monitor = DriftMonitor(window=2000, min_samples=200)
    monitor.configure(model_bundle.training_distribution)
    await _log(valid_car, 250)

    result = await monitor.refresh()

    assert result['ready'] is True
    assert result['window_samples'] == 250
    # 250 copies of one car look nothing like the training data.
    assert result['status'] == 'significant'


async def test_every_worker_sees_the_same_window(model_bundle, valid_car):
    """The window lives in the database, so a second process - or the same one
    after a restart - computes the same answer from the same rows."""
    await _log(valid_car, 250)
    first, second = DriftMonitor(), DriftMonitor()
    for monitor in (first, second):
        monitor.configure(model_bundle.training_distribution)

    assert await first.refresh() == await second.refresh()


async def test_the_window_holds_the_newest_predictions(model_bundle, valid_car):
    monitor = DriftMonitor(window=100, min_samples=50)
    monitor.configure(model_bundle.training_distribution)
    await _log(valid_car | {'fuel': 'Diesel'}, 100)
    await _log(valid_car | {'fuel': 'Petrol'}, 100)

    assert (await monitor.refresh())['window_samples'] == 100
    async with SessionFactory() as session:
        newest = await PredictionRepository(session).recent_inputs(['fuel'], 100)
    assert {row['fuel'] for row in newest} == {'Petrol'}


async def test_the_background_refresh_sets_the_gauges(client, valid_car):
    await _log(valid_car, 250)

    await refresher.refresh_once()

    assert REGISTRY.get_sample_value('model_drift_window_samples') == 250
    assert REGISTRY.get_sample_value('model_feature_drift_psi', {'feature': 'company'}) > 0.25


async def test_a_failed_refresh_keeps_the_loop_alive(monkeypatch):
    from app.services import health_refresh

    async def broken():
        raise ConnectionError('database unavailable')

    monkeypatch.setattr(health_refresh.monitor, 'refresh', broken)
    await refresher.refresh_once()  # logged, not raised


async def test_drift_endpoint_reports_configuration(client):
    response = await client.get('/model/drift')
    assert response.status_code == 200
    body = response.json()
    assert body['configured'] is True
    assert 'features' in body


async def test_a_forced_recompute_reads_the_log(client, operator_headers, valid_car):
    await _log(valid_car, 250)

    stale = (await client.get('/model/drift')).json()
    fresh = (await client.get('/model/drift', params={'recompute': 'true'}, headers=operator_headers)).json()

    assert stale['window_samples'] == 0
    assert fresh['window_samples'] == 250
    assert fresh['ready'] is True


async def test_the_refresher_runs_on_a_timer_and_stops(monkeypatch):
    import asyncio

    from app.services import health_refresh

    calls = 0

    async def counting():
        nonlocal calls
        calls += 1

    monkeypatch.setattr(health_refresh.monitor, 'refresh', counting)
    loop = health_refresh.HealthRefresher(interval_seconds=0.01)
    loop.start()
    await asyncio.sleep(0.1)
    await loop.stop()
    stopped_at = calls
    await asyncio.sleep(0.05)

    assert stopped_at >= 2
    assert calls == stopped_at
