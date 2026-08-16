"""add Goodiebag configuration and checkout status

Revision ID: 20260816_0016
Revises: 20260816_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0016"
down_revision: str | None = "20260816_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "goodiebag_offered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "shifts",
        sa.Column("goodiebag_override", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "checkins",
        sa.Column(
            "goodiebag_received",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("checkins", "goodiebag_received")
    op.drop_column("shifts", "goodiebag_override")
    op.drop_column("events", "goodiebag_offered")
