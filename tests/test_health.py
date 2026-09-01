async def test_health_is_liveness_only(client):
    response = await client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


async def test_ready_reports_every_dependency(client):
    response = await client.get('/ready')
    assert response.status_code == 200
    body = response.json()
    assert body['ready'] is True
    assert body['checks'] == {'database': True, 'redis': True, 'model': True}


async def test_ready_fails_when_model_is_missing(client):
    from app.main import app

    original = app.state.model
    app.state.model = None
    try:
        response = await client.get('/ready')
        assert response.status_code == 503
        assert response.json()['ready'] is False
        assert response.json()['checks']['model'] is False
    finally:
        app.state.model = original
