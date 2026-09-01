"""Data access layer.

Every query against the database lives here. Routes and services never hold a
session or build a statement themselves, which is what keeps the API, business
and data-access layers genuinely separate rather than nominally so.
"""

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Prediction, User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_username(self, username: str) -> User | None:
        result = await self._session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def create(self, username: str, hashed_password: str) -> User:
        user = User(username=username, hashed_password=hashed_password)
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user


class PredictionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def log(
        self,
        *,
        user_id: int | None,
        features: dict,
        predicted_price: float,
        cache_hit: bool,
    ) -> Prediction:
        prediction = Prediction(
            user_id=user_id,
            predicted_price=predicted_price,
            cache_hit=cache_hit,
            **features,
        )
        self._session.add(prediction)
        await self._session.commit()
        return prediction

    async def log_many(self, rows: list[dict]) -> int:
        """Insert a batch of prediction rows in a single statement.

        One round trip and one commit for the whole batch instead of per row.
        """
        if not rows:
            return 0
        await self._session.execute(insert(Prediction), rows)
        await self._session.commit()
        return len(rows)
