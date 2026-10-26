"""index prediction history by user

History now returns the caller's own predictions newest first:
WHERE user_id = ? ORDER BY created_at DESC. One index on
(user_id, created_at DESC) serves the filter and the order. It replaces the
single-column user_id index, which it covers, and the (company, year) index,
which no query uses any more.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        'ix_predictions_user_id_created_at',
        'predictions',
        ['user_id', sa.text('created_at DESC')],
    )
    op.drop_index('ix_predictions_user_id', table_name='predictions')
    op.drop_index('ix_predictions_company_year_created_at', table_name='predictions')


def downgrade() -> None:
    op.create_index(
        'ix_predictions_company_year_created_at',
        'predictions',
        ['company', 'year', sa.text('created_at DESC')],
    )
    op.create_index('ix_predictions_user_id', 'predictions', ['user_id'])
    op.drop_index('ix_predictions_user_id_created_at', table_name='predictions')
