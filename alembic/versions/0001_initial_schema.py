"""initial schema: users and predictions

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_users_username', 'users', ['username'], unique=True)

    op.create_table(
        'predictions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('company', sa.String(length=50), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('owner', sa.String(length=20), nullable=False),
        sa.Column('fuel', sa.String(length=10), nullable=False),
        sa.Column('seller_type', sa.String(length=20), nullable=False),
        sa.Column('transmission', sa.String(length=10), nullable=False),
        sa.Column('km_driven', sa.Float(), nullable=False),
        sa.Column('mileage_mpg', sa.Float(), nullable=False),
        sa.Column('engine_cc', sa.Float(), nullable=False),
        sa.Column('max_power_bhp', sa.Float(), nullable=False),
        sa.Column('torque_nm', sa.Float(), nullable=False),
        sa.Column('seats', sa.Float(), nullable=False),
        sa.Column('predicted_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('cache_hit', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_predictions_user_id', 'predictions', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_predictions_user_id', table_name='predictions')
    op.drop_table('predictions')
    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')
