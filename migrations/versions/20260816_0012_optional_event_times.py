"""track optional event start and end times

Revision ID: 20260816_0012
Revises: 20260816_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0012"
down_revision: str | None = "20260816_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing non-null event timestamps came from datetime-local fields and
    # therefore contain an explicitly supplied time. Null legacy timestamps
    # remain harmless; new form submissions set these flags deliberately.
    op.add_column(
        "events",
        sa.Column(
            "start_time_is_set",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "end_time_is_set",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "end_time_is_set")
    op.drop_column("events", "start_time_is_set")
