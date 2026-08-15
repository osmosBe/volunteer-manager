"""add structured materials for work areas

Revision ID: 20260815_0003
Revises: 20260815_0002
Create Date: 2026-08-15 14:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0003"
down_revision: str | None = "20260815_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "team_id",
            sa.Integer(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column(
            "quantity_required", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column(
            "quantity_available", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("unit", sa.String(40), server_default="Stück", nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "is_consumable", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("team_id", "name", name="uq_team_material_name"),
    )
    op.create_index("ix_team_materials_team_id", "team_materials", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_team_materials_team_id", table_name="team_materials")
    op.drop_table("team_materials")
