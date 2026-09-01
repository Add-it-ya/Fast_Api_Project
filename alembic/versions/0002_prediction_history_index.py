"""composite index for the prediction history query

Serves WHERE company = ? AND year = ? ORDER BY created_at DESC.

Column order matters: the two equality columns come first, then the sort
column in the direction the query asks for. That lets one index satisfy the
filter and return rows already ordered, so the planner drops the sort node
instead of reading the whole table and running a top-N heapsort.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        'ix_predictions_company_year_created_at',
        'predictions',
        ['company', 'year', sa.text('created_at DESC')],
    )


def downgrade() -> None:
    op.drop_index('ix_predictions_company_year_created_at', table_name='predictions')
