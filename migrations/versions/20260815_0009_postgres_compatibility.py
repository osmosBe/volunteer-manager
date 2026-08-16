"""expand event status storage for all current workflow values

Revision ID: 20260815_0009
Revises: 20260815_0008
Create Date: 2026-08-15 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0009"
down_revision: str | None = "20260815_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The first migration's non-native enum was sized from its then-longest value
    # (``archived``). PostgreSQL enforces that VARCHAR length while SQLite does not.
    # Batch mode keeps the operation portable for existing local SQLite databases.
    with op.batch_alter_table("events") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=8),
            type_=sa.String(length=32),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=8),
            existing_nullable=False,
        )
