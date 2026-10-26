from typing import get_args

import pytest

from app.schemas.prediction import Company, Fuel, Owner, SellerType, Transmission

SCHEMA_CATEGORIES = {
    'company': Company,
    'owner': Owner,
    'fuel': Fuel,
    'seller_type': SellerType,
    'transmission': Transmission,
}


@pytest.mark.parametrize(
    'field,value',
    [
        ('company', 'Tesla'),  # not in the training data
        ('company', 'Lexus'),  # one row in the data, none in the training split
        ('company', 'Ashok'),  # likewise
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


def test_accepted_categories_are_exactly_what_the_model_trained_on(model_bundle):
    """A value the encoder never saw would become an all-zero vector and a
    meaningless price; a trained value missing here would be refused for no
    reason. Either way the schema and the model have drifted apart."""
    pre = model_bundle.pipeline.named_steps['pre']
    columns = {name: cols for name, _, cols in pre.transformers_}['cat']
    encoder = pre.named_transformers_['cat'].named_steps['encoder']
    trained = {
        column: {str(c) for c in categories}
        for column, categories in zip(columns, encoder.categories_, strict=True)
    }

    assert {column: set(get_args(literal)) for column, literal in SCHEMA_CATEGORIES.items()} == trained
