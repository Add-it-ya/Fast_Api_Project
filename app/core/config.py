from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRETS = {'secret', 'changeme', 'change-me', 'password', 'demo', 'test'}

# libpq understands these; asyncpg does not, and raises on the ones it cannot
# map. sslmode has an asyncpg equivalent (ssl), channel_binding has none.
LIBPQ_ONLY_PARAMS = {'channel_binding'}


def normalise_database_url(url: str) -> str:
    """Accept the connection string a managed PostgreSQL actually hands out.

    Hosted providers give you a libpq URL - `postgresql://` with
    `sslmode=require` - which asyncpg cannot use as written. Rewriting it here
    means a deployment pastes the provider's string unedited rather than
    hand-editing a DSN in a dashboard, which is where typos live.
    """
    parts = urlsplit(url)

    scheme = parts.scheme
    if scheme in ('postgres', 'postgresql'):
        scheme = 'postgresql+asyncpg'

    if scheme != 'postgresql+asyncpg':
        return url

    query = [
        ('ssl' if key == 'sslmode' else key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in LIBPQ_ONLY_PARAMS
    ]

    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    """Application settings.

    API_KEY, JWT_SECRET_KEY, DATABASE_URL and REDIS_URL have no defaults on
    purpose: the app should refuse to boot rather than fall back to a value
    that is safe locally and dangerous in production.
    """

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        protected_namespaces=(),
    )

    PROJECT_NAME: str = 'Car Price Prediction API'

    API_KEY: str = Field(min_length=8)
    JWT_SECRET_KEY: str = Field(min_length=32)
    DATABASE_URL: str
    REDIS_URL: str

    JWT_ALGORITHM: str = 'HS256'
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Open sign-up is right for a local stack and wrong for a reachable one:
    # anyone who finds the URL can create accounts in it. Set this false and
    # /register carries the same API key as every other write endpoint.
    ALLOW_PUBLIC_REGISTRATION: bool = True

    # 'json' for shippable structured logs, 'text' when reading them by eye.
    LOG_FORMAT: str = 'json'

    MODEL_PATH: str = 'app/models/model.joblib'
    MODEL_METADATA_PATH: str = 'app/models/model_metadata.json'
    PREDICTION_CACHE_TTL_SECONDS: int = 3600

    RATE_LIMIT_REQUESTS: int = 60
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Sized per worker process. PostgreSQL defaults to max_connections=100, so
    # WEB_CONCURRENCY * (DB_POOL_SIZE + DB_MAX_OVERFLOW) must stay under it -
    # exceeding it surfaces as asyncpg TooManyConnectionsError under load.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    @field_validator('DATABASE_URL')
    @classmethod
    def use_the_async_driver(cls, value: str) -> str:
        return normalise_database_url(value)

    @field_validator('JWT_SECRET_KEY', 'API_KEY')
    @classmethod
    def reject_placeholder(cls, value: str, info) -> str:
        if value.strip().lower() in PLACEHOLDER_SECRETS:
            raise ValueError(
                f'{info.field_name} is set to a placeholder value; '
                'generate a real one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return value


@lru_cache
def get_settings() -> Settings:
    # The required fields come from the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
