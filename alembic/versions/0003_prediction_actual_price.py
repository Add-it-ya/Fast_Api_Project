"""record the real sale price against a prediction

Lets the service score its own predictions against reality instead of only
reporting the metrics it was trained with.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'predictions', sa.Column('actual_price', sa.Numeric(precision=12, scale=2), nullable=True)
    )
    # Only a small share of rows ever get an outcome reported, so a partial
    # index keeps the scoring query cheap without carrying the whole table.
    op.create_index(
        'ix_predictions_actual_price_created_at',
        'predictions',
        ['created_at'],
        postgresql_where=sa.text('actual_price IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_predictions_actual_price_created_at', table_name='predictions')
    op.drop_column('predictions', 'actual_price')
