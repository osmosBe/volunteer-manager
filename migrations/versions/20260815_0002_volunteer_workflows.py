"""add volunteer planning, public registration and check-in workflow tables

Revision ID: 20260815_0002
Revises: 20260630_0001
Create Date: 2026-08-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0002"
down_revision: str | None = "20260630_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive migration: it preserves the early DEV schema and its seed data.
    for column in (
        sa.Column("short_description", sa.String(500)),
        sa.Column("description", sa.Text()),
        sa.Column(
            "timezone", sa.String(80), server_default="Europe/Vienna", nullable=False
        ),
        sa.Column("venue", sa.String(200)),
        sa.Column("address", sa.String(500)),
        sa.Column("public_meeting_point", sa.String(300)),
        sa.Column("registration_opens_at", sa.DateTime(timezone=True)),
        sa.Column("registration_closes_at", sa.DateTime(timezone=True)),
        sa.Column("contact_name", sa.String(200)),
        sa.Column("contact_email", sa.String(255)),
        sa.Column("contact_phone", sa.String(50)),
        sa.Column("briefing", sa.Text()),
        sa.Column("clothing_and_material", sa.Text()),
        sa.Column("catering_info", sa.Text()),
        sa.Column("accessibility_info", sa.Text()),
        sa.Column("is_public", sa.Boolean(), server_default=sa.false(), nullable=False),
    ):
        op.add_column("events", column)

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("color", sa.String(20)),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lead_name", sa.String(200)),
        sa.Column("lead_contact", sa.String(255)),
        sa.Column("meeting_point", sa.String(300)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_teams_event_id", "teams", ["event_id"])
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "team_id",
            sa.Integer(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("short_description", sa.String(500)),
        sa.Column("description", sa.Text()),
        sa.Column("requirements", sa.Text()),
        sa.Column("minimum_age", sa.Integer()),
        sa.Column(
            "training_required", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "prefer_pair", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "physically_demanding",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "sensitive_task", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("default_meeting_point", sa.String(300)),
        sa.Column("materials", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_roles_team_id", "roles", ["team_id"])

    for column in (
        sa.Column("pronouns", sa.String(100)),
        sa.Column("emergency_contact_name", sa.String(200)),
        sa.Column("emergency_contact_phone", sa.String(50)),
        sa.Column("tshirt_size", sa.String(40)),
        sa.Column("dietary_needs", sa.Text()),
        sa.Column("accessibility_needs", sa.Text()),
        sa.Column("experience", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("internal_note", sa.Text()),
        sa.Column(
            "contact_consent", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "future_contact_consent",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("consented_at", sa.DateTime(timezone=True)),
        sa.Column("privacy_version", sa.String(80)),
        sa.Column("edit_token_hash", sa.String(128)),
        sa.Column("edit_token_revoked_at", sa.DateTime(timezone=True)),
        sa.Column("anonymized_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("volunteers", column)
    op.create_index(
        "ix_volunteers_edit_token_hash", "volunteers", ["edit_token_hash"], unique=True
    )

    # SQLite cannot add a foreign-key column through ALTER TABLE.  Batch mode
    # rebuilds the small ``shifts`` table while preserving its existing rows.
    with op.batch_alter_table("shifts") as batch_op:
        batch_op.add_column(sa.Column("role_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_shifts_role_id_roles",
            "roles",
            ["role_id"],
            ["id"],
            ondelete="SET NULL",
        )

    for column in (
        sa.Column("waitlist_capacity", sa.Integer()),
        sa.Column("lead_name", sa.String(200)),
        sa.Column("admin_notes", sa.Text()),
        sa.Column("volunteer_notes", sa.Text()),
        sa.Column("status", sa.String(32), server_default="draft", nullable=False),
    ):
        op.add_column("shifts", column)
    op.create_index("ix_shifts_role_id", "shifts", ["role_id"])

    for column in (
        sa.Column("source", sa.String(20), server_default="public", nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("checked_in_at", sa.DateTime(timezone=True)),
        sa.Column("internal_note", sa.Text()),
    ):
        op.add_column("shift_assignments", column)

    op.create_table(
        "checkins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("shift_assignments.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("checked_in_at", sa.DateTime(timezone=True)),
        sa.Column("checked_out_at", sa.DateTime(timezone=True)),
        sa.Column(
            "lanyard_issued", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "wristband_issued", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "radio_issued", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("other_issued", sa.Text()),
        sa.Column("materials_returned_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_checkins_assignment_id", "checkins", ["assignment_id"])
    op.create_table(
        "briefings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE")
        ),
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE")
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.String(40), server_default="1.0", nullable=False),
        sa.Column("visible_from", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_briefings_event_id", "briefings", ["event_id"])
    op.create_table(
        "briefing_confirmations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "briefing_id",
            sa.Integer(),
            sa.ForeignKey("briefings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "volunteer_id",
            sa.Integer(),
            sa.ForeignKey("volunteers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "briefing_id", "volunteer_id", name="uq_briefing_volunteer"
        ),
    )
    op.create_index(
        "ix_briefing_confirmations_briefing_id",
        "briefing_confirmations",
        ["briefing_id"],
    )
    op.create_index(
        "ix_briefing_confirmations_volunteer_id",
        "briefing_confirmations",
        ["volunteer_id"],
    )
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "volunteer_id",
            sa.Integer(),
            sa.ForeignKey("volunteers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_outbox_messages_volunteer_id", "outbox_messages", ["volunteer_id"]
    )


def downgrade() -> None:
    for table in ("outbox_messages", "briefing_confirmations", "briefings", "checkins"):
        op.drop_table(table)
    # SQLite cannot safely remove the additive columns without a table rebuild;
    # downgrades are intentionally data-preserving no-ops for those columns.
