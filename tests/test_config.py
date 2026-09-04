import pytest

from app.core.config import normalise_database_url

NEON = 'ep-quiet-block-123.ap-southeast-1.aws.neon.tech'


@pytest.mark.parametrize(
    'given,expected',
    [
        # Already correct, and left alone.
        (
            'postgresql+asyncpg://carprice:carprice@localhost:5432/carprice',
            'postgresql+asyncpg://carprice:carprice@localhost:5432/carprice',
        ),
        # The bare scheme a provider prints, with no driver.
        (
            'postgresql://user:pw@localhost:5432/db',
            'postgresql+asyncpg://user:pw@localhost:5432/db',
        ),
        # The legacy alias some providers still emit.
        ('postgres://user:pw@localhost:5432/db', 'postgresql+asyncpg://user:pw@localhost:5432/db'),
        # libpq spells it sslmode; asyncpg spells it ssl.
        (
            f'postgresql://user:pw@{NEON}/neondb?sslmode=require',
            f'postgresql+asyncpg://user:pw@{NEON}/neondb?ssl=require',
        ),
        # channel_binding has no asyncpg equivalent and must not be passed on.
        (
            f'postgresql://user:pw@{NEON}/neondb?sslmode=require&channel_binding=require',
            f'postgresql+asyncpg://user:pw@{NEON}/neondb?ssl=require',
        ),
    ],
)
def test_provider_connection_strings_become_asyncpg_dsns(given, expected):
    assert normalise_database_url(given) == expected


def test_a_non_postgres_url_is_left_alone():
    assert normalise_database_url('sqlite+aiosqlite:///./local.db') == 'sqlite+aiosqlite:///./local.db'


def test_a_password_with_url_characters_survives():
    url = 'postgresql://user:p%40ss%2Fword@host:5432/db?sslmode=require'
    assert normalise_database_url(url) == 'postgresql+asyncpg://user:p%40ss%2Fword@host:5432/db?ssl=require'
