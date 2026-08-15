"""add event-level participation rule for minors

Revision ID: 20260815_0007
Revises: 20260815_0006
Create Date: 2026-08-15 15:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0007"
down_revision: str | None = "20260815_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "allows_minors", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "allows_minors")
