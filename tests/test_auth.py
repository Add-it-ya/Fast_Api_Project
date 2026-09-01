import pytest
from sqlalchemy import text

from app.db.session import engine


async def test_register_returns_user_without_password(client):
    response = await client.post('/register', json={'username': 'alice', 'password': 'a-good-password'})
    assert response.status_code == 201
    body = response.json()
    assert body['username'] == 'alice'
    assert 'password' not in body
    assert 'hashed_password' not in body


async def test_password_is_stored_as_a_bcrypt_hash(client):
    await client.post('/register', json={'username': 'alice', 'password': 'a-good-password'})

    async with engine.connect() as conn:
        stored = await conn.scalar(
            text('SELECT hashed_password FROM users WHERE username = :u'), {'u': 'alice'}
        )

    assert stored != 'a-good-password'
    assert stored.startswith('$2b$')
    assert len(stored) == 60


async def test_duplicate_username_is_rejected(client):
    payload = {'username': 'alice', 'password': 'a-good-password'}
    await client.post('/register', json=payload)
    response = await client.post('/register', json=payload)
    assert response.status_code == 409


async def test_login_returns_a_bearer_token(client, registered_user):
    response = await client.post('/login', json=registered_user)
    assert response.status_code == 200
    body = response.json()
    assert body['token_type'] == 'bearer'
    assert body['expires_in'] > 0
    assert body['access_token']


@pytest.mark.parametrize(
    'username,password',
    [
        ('tester', 'wrong-password-here'),
        ('nobody', 'test-password-1'),
    ],
)
async def test_bad_credentials_are_rejected(client, registered_user, username, password):
    response = await client.post('/login', json={'username': username, 'password': password})
    assert response.status_code == 401
    # The same message either way, so it does not leak which usernames exist.
    assert response.json()['detail'] == 'Invalid username or password'


@pytest.mark.parametrize(
    'payload',
    [
        {'username': 'ab', 'password': 'a-good-password'},
        {'username': 'has space', 'password': 'a-good-password'},
        {'username': 'alice', 'password': 'short'},
        {'username': 'alice', 'password': 'x' * 73},
    ],
)
async def test_registration_input_is_validated(client, payload):
    response = await client.post('/register', json=payload)
    assert response.status_code == 422


async def test_me_requires_a_token(client):
    response = await client.get('/me')
    assert response.status_code == 401


async def test_me_returns_the_authenticated_user(client, token):
    response = await client.get('/me', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200
    assert response.json()['username'] == 'tester'
