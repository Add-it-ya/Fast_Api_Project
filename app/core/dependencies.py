import secrets

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import InvalidApiKeyError, InvalidTokenError
from app.core.security import decode_access_token
from app.db.models import User
from app.db.repositories import PredictionRepository, UserRepository
from app.db.session import get_session
from app.services.auth_service import AuthService
from app.services.model_service import PredictionService

# auto_error=False so a missing header raises our 401 instead of the 422 that
# a bare Header(...) produces - a missing credential is not a schema problem.
api_key_scheme = APIKeyHeader(name='api-key', auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def get_user_repository(session: AsyncSession = Depends(get_session)) -> UserRepository:
    return UserRepository(session)


def get_prediction_repository(session: AsyncSession = Depends(get_session)) -> PredictionRepository:
    return PredictionRepository(session)


async def require_api_key(api_key: str | None = Depends(api_key_scheme)) -> str:
    if not api_key or not secrets.compare_digest(api_key, settings.API_KEY):
        raise InvalidApiKeyError()
    return api_key


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    users: UserRepository = Depends(get_user_repository),
) -> User:
    if credentials is None:
        raise InvalidTokenError('Missing bearer token')

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise InvalidTokenError()

    user_id = payload.get('uid')
    if not isinstance(user_id, int):
        raise InvalidTokenError()

    user = await users.get_by_id(user_id)
    if user is None:
        raise InvalidTokenError('User no longer exists')
    return user


def get_auth_service(users: UserRepository = Depends(get_user_repository)) -> AuthService:
    return AuthService(users)


def get_prediction_service(
    request: Request,
    predictions: PredictionRepository = Depends(get_prediction_repository),
) -> PredictionService:
    return PredictionService(getattr(request.app.state, 'model', None), predictions)
