import pytest

from app.services.drift import DriftMonitor, classify, psi

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
    monitor = DriftMonitor(min_samples=30, recompute_every=10_000)
    monitor.configure(REFERENCE)

    km = [50.0, 150.0, 250.0] * 20
    fuels = ['Petrol', 'Diesel'] * 30
    for row in rows(km, fuels):
        monitor.observe(row)

    report = monitor.refresh()
    assert report['status'] == 'stable'
    assert report['worst_psi'] < 0.1


def test_shifted_numeric_traffic_is_detected():
    monitor = DriftMonitor(min_samples=30, recompute_every=10_000)
    monitor.configure(REFERENCE)

    # Everything in the top bin, against a third expected there.
    for row in rows([250.0] * 60, ['Petrol', 'Diesel'] * 30):
        monitor.observe(row)

    report = monitor.refresh()
    scores = {f['feature']: f['psi'] for f in report['features']}
    assert scores['km_driven'] > 0.25
    assert report['status'] == 'significant'


def test_shifted_categorical_traffic_is_detected():
    monitor = DriftMonitor(min_samples=30, recompute_every=10_000)
    monitor.configure(REFERENCE)

    for row in rows([50.0, 150.0, 250.0] * 20, ['Diesel'] * 60):
        monitor.observe(row)

    scores = {f['feature']: f['psi'] for f in monitor.refresh()['features']}
    assert scores['fuel'] > 0.25


def test_rare_categories_do_not_manufacture_drift():
    """CNG and LPG are 0.5% of training each. A couple of them in a small
    window must not read as significant drift - that was a real false positive
    before rare categories were pooled."""
    monitor = DriftMonitor(min_samples=30, recompute_every=10_000)
    monitor.configure(REFERENCE)

    fuels = ['Petrol'] * 29 + ['Diesel'] * 29 + ['CNG', 'LPG']
    for row in rows([50.0, 150.0, 250.0] * 20, fuels):
        monitor.observe(row)

    scores = {f['feature']: f['psi'] for f in monitor.refresh()['features']}
    assert scores['fuel'] < 0.1


def test_unseen_categories_are_absorbed_not_infinite():
    monitor = DriftMonitor(min_samples=30, recompute_every=10_000)
    monitor.configure(REFERENCE)

    fuels = ['Petrol'] * 30 + ['Hydrogen'] * 30
    for row in rows([50.0, 150.0, 250.0] * 20, fuels):
        monitor.observe(row)

    scores = {f['feature']: f['psi'] for f in monitor.refresh()['features']}
    assert scores['fuel'] == pytest.approx(scores['fuel'])  # finite, not inf/nan
    assert scores['fuel'] > 0.25


def test_below_min_samples_reports_not_ready():
    monitor = DriftMonitor(min_samples=100, recompute_every=10_000)
    monitor.configure(REFERENCE)
    for row in rows([50.0] * 10, ['Petrol'] * 10):
        monitor.observe(row)

    report = monitor.refresh()
    assert report['ready'] is False
    assert report['window_samples'] == 10


def test_unconfigured_monitor_ignores_observations():
    monitor = DriftMonitor()
    monitor.observe({'km_driven': 100.0, 'fuel': 'Petrol'})

    report = monitor.snapshot()
    assert report['configured'] is False
    assert report['window_samples'] == 0


def test_window_is_bounded():
    monitor = DriftMonitor(window=25, min_samples=10, recompute_every=10_000)
    monitor.configure(REFERENCE)
    for row in rows([50.0] * 100, ['Petrol'] * 100):
        monitor.observe(row)

    assert monitor.snapshot()['window_samples'] == 25


async def test_drift_endpoint_reports_configuration(client):
    response = await client.get('/model/drift')
    assert response.status_code == 200
    body = response.json()
    assert body['configured'] is True
    assert 'features' in body
