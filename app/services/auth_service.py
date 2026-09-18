"""Business logic for registration and login.

bcrypt is slow on purpose - that is what makes a stolen hash expensive to
crack - so every hash and verify runs in a worker thread. Called directly from
an async handler it would block the event loop, and every other request on
the worker, for the whole of each call.
"""

import asyncio

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
        hashed = await asyncio.to_thread(hash_password, password)
        return await self._users.create(username, hashed)

    async def authenticate(self, username: str, password: str) -> tuple[str, int]:
        user = await self._users.get_by_username(username)

        # Verify against a dummy hash when the user is missing so the response
        # time does not reveal which usernames exist.
        if user is None:
            await asyncio.to_thread(hash_password, 'not-a-real-password')
            raise InvalidCredentialsError()

        if not await asyncio.to_thread(verify_password, password, user.hashed_password):
            raise InvalidCredentialsError()

        token = create_access_token(subject=user.username, user_id=user.id)
        return token, settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
