"""add SMTP configuration and outbox delivery state

Revision ID: 20260815_0004
Revises: 20260815_0003
Create Date: 2026-08-15 14:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0004"
down_revision: str | None = "20260815_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "smtp_configurations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), server_default="587", nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("from_email", sa.String(255), nullable=False),
        sa.Column(
            "from_name",
            sa.String(255),
            server_default="ST. PRIDE Volunteer Management",
            nullable=False,
        ),
        sa.Column(
            "use_starttls", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column("use_ssl", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("outbox_messages", sa.Column("recipient_email", sa.String(255)))
    op.add_column("outbox_messages", sa.Column("sent_at", sa.DateTime(timezone=True)))
    op.add_column("outbox_messages", sa.Column("last_error", sa.String(500)))


def downgrade() -> None:
    op.drop_column("outbox_messages", "last_error")
    op.drop_column("outbox_messages", "sent_at")
    op.drop_column("outbox_messages", "recipient_email")
    op.drop_table("smtp_configurations")
