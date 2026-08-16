"""add configurable mail templates and delivery flow

Revision ID: 20260815_0008
Revises: 20260815_0007
Create Date: 2026-08-15 15:55:00.000000
"""

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0008"
down_revision: str | None = "20260815_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_messages",
        sa.Column(
            "delivery_mode", sa.String(20), nullable=False, server_default="manual"
        ),
    )
    op.create_table(
        "mail_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("trigger_description", sa.String(500), nullable=False),
        sa.Column("subject_template", sa.String(255), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column(
            "delivery_mode", sa.String(20), nullable=False, server_default="manual"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_mail_templates_key"),
    )
    op.create_index("ix_mail_templates_key", "mail_templates", ["key"], unique=True)
    template_table = sa.table(
        "mail_templates",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("trigger_description", sa.String),
        sa.column("subject_template", sa.String),
        sa.column("body_template", sa.Text),
        sa.column("delivery_mode", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    timestamp = datetime(2026, 8, 15, 15, 55, tzinfo=timezone.utc)
    op.bulk_insert(
        template_table,
        [
            {
                "key": "email_verification",
                "name": "E-Mail-Adresse bestätigen",
                "trigger_description": "Nach einer öffentlichen Anmeldung.",
                "subject_template": "E-Mail für {event_name} bestätigen",
                "body_template": (
                    "Hallo {first_name},\n\ndeine Anmeldung für {event_name} "
                    "wurde erfasst. Bitte bestätige deine E-Mail-Adresse:\n"
                    "{verification_url}\n\nDeine Anmeldung verwalten:\n{edit_url}"
                    "\n\nST. PRIDE"
                ),
                "delivery_mode": "automatic",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            {
                "key": "email_changed_verification",
                "name": "Geänderte E-Mail-Adresse bestätigen",
                "trigger_description": "Nach Änderung der E-Mail-Adresse.",
                "subject_template": ("Neue E-Mail-Adresse für {event_name} bestätigen"),
                "body_template": (
                    "Hallo {first_name},\n\nbitte bestätige deine neue "
                    "E-Mail-Adresse für {event_name}:\n{verification_url}\n\n"
                    "Dein bestätigter Anmelde- und Schichtstatus bleibt unverändert."
                    "\n\nST. PRIDE"
                ),
                "delivery_mode": "automatic",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            {
                "key": "registration_updated",
                "name": "Anmeldung geändert",
                "trigger_description": "Nach Änderung von Kontaktdaten oder Schichten.",
                "subject_template": (
                    "Deine Anmeldung für {event_name} wurde aktualisiert"
                ),
                "body_template": (
                    "Hallo {first_name},\n\ndeine Kontaktdaten oder Schichten für "
                    "{event_name} wurden aktualisiert.\n\nST. PRIDE"
                ),
                "delivery_mode": "manual",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            {
                "key": "registration_cancellation",
                "name": "Schicht storniert",
                "trigger_description": "Wenn eine Schicht storniert wird.",
                "subject_template": "Schicht bei {event_name} storniert",
                "body_template": (
                    "Hallo {first_name},\n\ndeine Schicht „{shift_title}“ bei "
                    "{event_name} wurde storniert.\n\nST. PRIDE"
                ),
                "delivery_mode": "manual",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            {
                "key": "registration_rejected",
                "name": "Anmeldung abgelehnt",
                "trigger_description": "Wenn die Administration eine Person ablehnt.",
                "subject_template": (
                    "Rückmeldung zu deiner Anmeldung für {event_name}"
                ),
                "body_template": (
                    "Hallo {first_name},\n\nleider können wir deine Anmeldung für "
                    "{event_name} nicht annehmen.\nBegründung: {reason}\n\n"
                    "Bei Rückfragen: {contact_email}\n\nST. PRIDE"
                ),
                "delivery_mode": "automatic",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            {
                "key": "waitlist_promoted",
                "name": "Von Warteliste nachgerückt",
                "trigger_description": "Beim manuellen Nachrücken auf einen Platz.",
                "subject_template": "Freier Platz bei {event_name}",
                "body_template": (
                    "Hallo {first_name},\n\ndu bist für die Schicht „{shift_title}“ "
                    "bei {event_name} von der Warteliste nachgerückt und nun "
                    "bestätigt.\n\nST. PRIDE"
                ),
                "delivery_mode": "automatic",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_mail_templates_key", table_name="mail_templates")
    op.drop_table("mail_templates")
    op.drop_column("outbox_messages", "delivery_mode")
