"""Business logic for registration and login."""

from app.core.config import settings
from app.core.exceptions import InvalidCredentialsError, UserAlreadyExistsError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.db.repositories import UserRepository


class AuthService:
    def __init__(self, users: UserRepository):
        self._users = users

    async def register(self, username: str, password: str) -> User:
        if await self._users.get_by_username(username) is not None:
            raise UserAlreadyExistsError()
        return await self._users.create(username, hash_password(password))

    async def authenticate(self, username: str, password: str) -> tuple[str, int]:
        user = await self._users.get_by_username(username)

        # Verify against a dummy hash when the user is missing so the response
        # time does not reveal which usernames exist.
        if user is None:
            hash_password('not-a-real-password')
            raise InvalidCredentialsError()

        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError()

        token = create_access_token(subject=user.username, user_id=user.id)
        return token, settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
