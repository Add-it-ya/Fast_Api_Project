"""Data access layer.

Every query against the database lives here. Routes and services never hold a
session or build a statement themselves, which is what keeps the API, business
and data-access layers genuinely separate rather than nominally so.
"""

from sqlalchemy import func, insert, select
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

    async def history(self, *, company: str, year: int, limit: int, offset: int) -> list[Prediction]:
        """Most recent predictions for a company and model year.

        The equality filters and the sort are served by one composite index on
        (company, year, created_at DESC) - see docs/query_optimisation.md.
        """
        statement = (
            select(Prediction)
            .where(Prediction.company == company, Prediction.year == year)
            .order_by(Prediction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def record_actual(self, prediction_id: int, actual_price: float) -> Prediction | None:
        prediction = await self._session.get(Prediction, prediction_id)
        if prediction is None:
            return None
        prediction.actual_price = actual_price
        await self._session.commit()
        await self._session.refresh(prediction)
        return prediction

    async def live_performance(self, limit: int = 1000) -> dict:
        """Error over the most recent predictions that have a reported outcome.

        This is the model scored against reality, as opposed to the validation
        metrics it was trained with.
        """
        recent = (
            select(
                Prediction.predicted_price.label('predicted'),
                Prediction.actual_price.label('actual'),
            )
            .where(Prediction.actual_price.is_not(None))
            .order_by(Prediction.created_at.desc())
            .limit(limit)
            .subquery()
        )

        error = func.abs(recent.c.predicted - recent.c.actual)
        result = await self._session.execute(
            select(
                func.count().label('scored'),
                func.avg(error).label('mae'),
                func.avg(error / func.nullif(recent.c.actual, 0) * 100).label('mape'),
            ).select_from(recent)
        )
        row = result.one()
        return {
            'scored': int(row.scored or 0),
            'mae': round(float(row.mae), 2) if row.mae is not None else None,
            'mape_pct': round(float(row.mape), 2) if row.mape is not None else None,
        }

    async def log_many(self, rows: list[dict]) -> int:
        """Insert a batch of prediction rows in a single statement.

        One round trip and one commit for the whole batch instead of per row.
        """
        if not rows:
            return 0
        await self._session.execute(insert(Prediction), rows)
        await self._session.commit()
        return len(rows)
