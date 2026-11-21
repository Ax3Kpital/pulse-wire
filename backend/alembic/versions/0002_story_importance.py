"""story importance score

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("stories") as batch_op:
        batch_op.add_column(sa.Column("importance", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("importance_tier", sa.String(length=16), nullable=True))
        batch_op.add_column(
            sa.Column("importance_reasons", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.create_index("ix_stories_importance", ["importance"])


def downgrade() -> None:
    with op.batch_alter_table("stories") as batch_op:
        batch_op.drop_index("ix_stories_importance")
        batch_op.drop_column("importance_reasons")
        batch_op.drop_column("importance_tier")
        batch_op.drop_column("importance")
