"""public id for predictions

/predict answers before its row is written, so the id a client gets back is
made by the API at predict time rather than by the database sequence. Existing
rows get one generated here.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'predictions',
        sa.Column('public_id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    )
    op.create_index('ix_predictions_public_id', 'predictions', ['public_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_predictions_public_id', table_name='predictions')
    op.drop_column('predictions', 'public_id')
