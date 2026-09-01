from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Prediction(Base):
    __tablename__ = 'predictions'

    # Equality columns first, then the sort column in the order the query asks
    # for it, so one index serves both the WHERE and the ORDER BY and the
    # planner can drop the sort node entirely.
    __table_args__ = (
        Index(
            'ix_predictions_company_year_created_at',
            'company',
            'year',
            text('created_at DESC'),
        ),
        Index(
            'ix_predictions_actual_price_created_at',
            'created_at',
            postgresql_where=text('actual_price IS NOT NULL'),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True
    )

    company: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    owner: Mapped[str] = mapped_column(String(20), nullable=False)
    fuel: Mapped[str] = mapped_column(String(10), nullable=False)
    seller_type: Mapped[str] = mapped_column(String(20), nullable=False)
    transmission: Mapped[str] = mapped_column(String(10), nullable=False)
    km_driven: Mapped[float] = mapped_column(Float, nullable=False)
    mileage_mpg: Mapped[float] = mapped_column(Float, nullable=False)
    engine_cc: Mapped[float] = mapped_column(Float, nullable=False)
    max_power_bhp: Mapped[float] = mapped_column(Float, nullable=False)
    torque_nm: Mapped[float] = mapped_column(Float, nullable=False)
    seats: Mapped[float] = mapped_column(Float, nullable=False)

    predicted_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Filled in later, if the real sale price is ever reported back. This is
    # what makes the prediction log a monitoring asset rather than an archive.
    actual_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
