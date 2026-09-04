from fastapi import APIRouter, Depends, status

from app.core.dependencies import allow_registration, get_auth_service, get_current_user
from app.core.rate_limit import enforce_rate_limit
from app.db.models import User
from app.schemas.auth import Credentials, TokenResponse, UserResponse
from app.services.auth_service import AuthService

router = APIRouter()


@router.post(
    '/register',
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(allow_registration), Depends(enforce_rate_limit)],
)
async def register(
    credentials: Credentials,
    auth: AuthService = Depends(get_auth_service),
):
    return await auth.register(credentials.username, credentials.password)


@router.post('/login', response_model=TokenResponse, dependencies=[Depends(enforce_rate_limit)])
async def login(
    credentials: Credentials,
    auth: AuthService = Depends(get_auth_service),
):
    token, expires_in = await auth.authenticate(credentials.username, credentials.password)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get('/me', response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user
