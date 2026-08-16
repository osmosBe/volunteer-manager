"""track optional registration opening and closing times

Revision ID: 20260816_0013
Revises: 20260816_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0013"
down_revision: str | None = "20260816_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing non-null registration timestamps contain explicitly supplied
    # times because the previous form used datetime-local inputs. New form
    # submissions set these flags deliberately when a time is omitted.
    op.add_column(
        "events",
        sa.Column(
            "registration_open_time_is_set",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "registration_close_time_is_set",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "registration_close_time_is_set")
    op.drop_column("events", "registration_open_time_is_set")
