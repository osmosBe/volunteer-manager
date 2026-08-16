"""add database-backed legal footer links

Revision ID: 20260816_0015
Revises: 20260816_0014
Create Date: 2026-08-16 20:55:00.000000
"""

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0015"
down_revision: str | None = "20260816_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legal_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("privacy_url", sa.String(length=2048)),
        sa.Column("imprint_url", sa.String(length=2048)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_legal_settings_singleton"),
    )
    now = datetime.now(timezone.utc)
    op.get_bind().execute(
        sa.text(
            "INSERT INTO legal_settings "
            "(id, privacy_url, imprint_url, created_at, updated_at) "
            "VALUES (:id, NULL, NULL, :created_at, :updated_at)"
        ),
        {"id": 1, "created_at": now, "updated_at": now},
    )


def downgrade() -> None:
    op.drop_table("legal_settings")
