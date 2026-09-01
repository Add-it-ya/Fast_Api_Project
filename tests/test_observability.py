import json
import logging

from app.core.logging_config import JsonFormatter, RequestIdFilter, request_id


async def test_response_carries_a_request_id(client):
    response = await client.get('/health')
    assert response.headers['x-request-id']


async def test_inbound_request_id_is_honoured(client):
    """A trace started upstream should survive into this service rather than
    being replaced."""
    response = await client.get('/health', headers={'X-Request-ID': 'upstream-trace-1'})
    assert response.headers['x-request-id'] == 'upstream-trace-1'


async def test_request_ids_differ_between_requests(client):
    first = await client.get('/health')
    second = await client.get('/health')
    assert first.headers['x-request-id'] != second.headers['x-request-id']


def test_json_formatter_emits_parseable_lines():
    record = logging.LogRecord('svc', logging.INFO, __file__, 1, 'hello %s', ('world',), None)
    record.request_id = 'abc123'

    payload = json.loads(JsonFormatter().format(record))
    assert payload['message'] == 'hello world'
    assert payload['level'] == 'INFO'
    assert payload['logger'] == 'svc'
    assert payload['request_id'] == 'abc123'
    assert payload['ts']


def test_json_formatter_promotes_extras_to_fields():
    record = logging.LogRecord('svc', logging.INFO, __file__, 1, 'done', (), None)
    record.status = 200
    record.duration_ms = 12.5

    payload = json.loads(JsonFormatter().format(record))
    assert payload['status'] == 200
    assert payload['duration_ms'] == 12.5


def test_json_formatter_includes_exceptions():
    try:
        raise ValueError('boom')
    except ValueError:
        import sys

        record = logging.LogRecord('svc', logging.ERROR, __file__, 1, 'failed', (), sys.exc_info())

    payload = json.loads(JsonFormatter().format(record))
    assert 'ValueError: boom' in payload['exception']


def test_filter_stamps_the_current_request_id():
    token = request_id.set('ctx-42')
    try:
        record = logging.LogRecord('svc', logging.INFO, __file__, 1, 'x', (), None)
        RequestIdFilter().filter(record)
        assert record.request_id == 'ctx-42'
    finally:
        request_id.reset(token)


async def test_prediction_metrics_are_exported(client, auth_headers, valid_car):
    await client.post('/predict', json=valid_car, headers=auth_headers)

    body = (await client.get('/metrics')).text
    assert 'prediction_latency_seconds' in body
    assert 'model_inference_seconds' in body
    assert 'predictions_total' in body
    assert 'model_version' in body


async def test_cache_outcome_is_labelled_separately(client, auth_headers, valid_car):
    await client.post('/predict', json=valid_car, headers=auth_headers)
    await client.post('/predict', json=valid_car, headers=auth_headers)

    body = (await client.get('/metrics')).text
    # Without the label a p95 cannot distinguish a Redis hit from a model call.
    assert 'cache="miss"' in body
    assert 'cache="hit"' in body


async def test_predictions_are_counted_by_company(client, auth_headers, valid_car):
    await client.post('/predict', json=valid_car | {'company': 'BMW'}, headers=auth_headers)

    body = (await client.get('/metrics')).text
    assert 'company="BMW"' in body
