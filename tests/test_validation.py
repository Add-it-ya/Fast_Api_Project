import pytest


@pytest.mark.parametrize(
    'field,value',
    [
        ('company', 'Tesla'),  # not in the training data
        ('company', ''),
        ('fuel', 'Nuclear'),
        ('owner', 'Fifth'),
        ('seller_type', 'Broker'),
        ('transmission', 'CVT'),
        ('year', -5),
        ('year', 1800),
        ('year', 3000),
        ('km_driven', -1),
        ('km_driven', 0),
        ('engine_cc', 0),
        ('max_power_bhp', -10),
        ('torque_nm', 0),
        ('seats', 0),
        ('seats', 99),
        ('mileage_mpg', -1),
    ],
)
async def test_out_of_range_values_are_rejected(client, auth_headers, valid_car, field, value):
    payload = valid_car | {field: value}
    response = await client.post('/predict', json=payload, headers=auth_headers)
    assert response.status_code == 422, f'{field}={value!r} should have been rejected'


async def test_missing_field_is_rejected(client, auth_headers, valid_car):
    payload = {k: v for k, v in valid_car.items() if k != 'engine_cc'}
    response = await client.post('/predict', json=payload, headers=auth_headers)
    assert response.status_code == 422


async def test_valid_payload_is_accepted(client, auth_headers, valid_car):
    response = await client.post('/predict', json=valid_car, headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.parametrize('company', ['Maruti', 'BMW', 'Mercedes-Benz', 'Tata'])
async def test_known_companies_are_accepted(client, auth_headers, valid_car, company):
    response = await client.post('/predict', json=valid_car | {'company': company}, headers=auth_headers)
    assert response.status_code == 200
