"""add e-mail verification state

Revision ID: 20260815_0005
Revises: 20260815_0004
Create Date: 2026-08-15 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0005"
down_revision: str | None = "20260815_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "volunteers", sa.Column("email_verified_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "volunteers", sa.Column("email_verification_token_hash", sa.String(128))
    )
    op.add_column(
        "volunteers",
        sa.Column("email_verification_sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_volunteers_email_verification_token_hash",
        "volunteers",
        ["email_verification_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_volunteers_email_verification_token_hash", table_name="volunteers"
    )
    op.drop_column("volunteers", "email_verification_sent_at")
    op.drop_column("volunteers", "email_verification_token_hash")
    op.drop_column("volunteers", "email_verified_at")
