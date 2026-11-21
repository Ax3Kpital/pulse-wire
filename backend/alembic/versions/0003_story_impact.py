"""replace the 0-100 importance score with a 1-10 impact score, colour level and sentiment

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("stories") as batch_op:
        batch_op.drop_index("ix_stories_importance")
        batch_op.drop_column("importance_reasons")
        batch_op.drop_column("importance_tier")
        batch_op.drop_column("importance")
        batch_op.add_column(
            sa.Column("impact_score", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("impact_level", sa.String(length=16), nullable=False, server_default="low")
        )
        batch_op.add_column(
            sa.Column("impact_reasons", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("sentiment", sa.Float(), nullable=False, server_default="0"))
        batch_op.create_index("ix_stories_impact_score", ["impact_score"])


def downgrade() -> None:
    with op.batch_alter_table("stories") as batch_op:
        batch_op.drop_index("ix_stories_impact_score")
        batch_op.drop_column("sentiment")
        batch_op.drop_column("impact_reasons")
        batch_op.drop_column("impact_level")
        batch_op.drop_column("impact_score")
        batch_op.add_column(sa.Column("importance", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("importance_tier", sa.String(length=16), nullable=True))
        batch_op.add_column(
            sa.Column("importance_reasons", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.create_index("ix_stories_importance", ["importance"])
