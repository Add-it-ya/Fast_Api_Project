import uuid
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
    Uuid,
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

    # History is one user's rows, newest first: the equality column leads and
    # the sort column follows in the order the query asks for, so one index
    # serves both the WHERE and the ORDER BY and there is no sort step. With
    # user_id leading it also covers the foreign key's lookups.
    __table_args__ = (
        Index('ix_predictions_user_id_created_at', 'user_id', text('created_at DESC')),
        Index(
            'ix_predictions_actual_price_created_at',
            'created_at',
            postgresql_where=text('actual_price IS NOT NULL'),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # The id clients see. /predict answers before its row is written, so the
    # database sequence cannot supply it; the API makes it at predict time.
    # The default covers rows inserted without one, such as the seed script's.
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, unique=True, index=True, nullable=False, server_default=text('gen_random_uuid()')
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

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
    # Filled in later if an operator reports the real sale price; the live
    # error figures are computed from these.
    actual_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
