"""Data access layer.

Every query against the database lives here. Routes and services never hold a
session or build a statement themselves, which is what keeps the API, business
and data-access layers genuinely separate rather than nominally so.
"""

import uuid

from sqlalchemy import func, insert, select, update
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

    async def history(
        self,
        *,
        user_id: int,
        company: str | None = None,
        year: int | None = None,
        limit: int,
        offset: int,
    ) -> list[Prediction]:
        """One user's predictions, newest first, optionally narrowed to a brand
        or model year.

        Served by the (user_id, created_at DESC) index - see
        docs/query_optimisation.md.
        """
        statement = select(Prediction).where(Prediction.user_id == user_id)
        if company is not None:
            statement = statement.where(Prediction.company == company)
        if year is not None:
            statement = statement.where(Prediction.year == year)
        statement = statement.order_by(Prediction.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def record_actual(self, public_id: uuid.UUID, actual_price: float) -> float | None:
        """Set the sale price if none is recorded yet; return the predicted
        price it is scored against, or None when nothing was updated.

        One conditional UPDATE, so two reports racing for the same prediction
        cannot both succeed.
        """
        statement = (
            update(Prediction)
            .where(Prediction.public_id == public_id, Prediction.actual_price.is_(None))
            .values(actual_price=actual_price)
            .returning(Prediction.predicted_price)
            .execution_options(synchronize_session=False)
        )
        predicted = (await self._session.execute(statement)).scalar_one_or_none()
        await self._session.commit()
        return float(predicted) if predicted is not None else None

    async def exists(self, public_id: uuid.UUID) -> bool:
        found = await self._session.scalar(select(Prediction.id).where(Prediction.public_id == public_id))
        return found is not None

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
