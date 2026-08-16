"""Admin-configurable plain-text mail templates and delivery rules."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MailTemplate, OutboxMessage, Volunteer

AUTOMATIC = "automatic"
MANUAL = "manual"
DISABLED = "disabled"
DELIVERY_MODES = {AUTOMATIC, MANUAL, DISABLED}
PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")
ALLOWED_PLACEHOLDERS = {
    "first_name",
    "last_name",
    "event_name",
    "verification_url",
    "edit_url",
    "shift_title",
    "reason",
    "contact_email",
}


@dataclass(frozen=True)
class TemplateDefinition:
    key: str
    name: str
    trigger_description: str
    subject: str
    body: str
    delivery_mode: str


DEFAULT_TEMPLATES = (
    TemplateDefinition(
        key="email_verification",
        name="E-Mail-Adresse bestätigen",
        trigger_description=(
            "Nach einer öffentlichen Anmeldung und nach Änderung der E-Mail-Adresse."
        ),
        subject="E-Mail für {event_name} bestätigen",
        body=(
            "Hallo {first_name},\n\n"
            "deine Anmeldung für {event_name} wurde erfasst. Bitte bestätige "
            "deine E-Mail-Adresse:\n{verification_url}\n\n"
            "Deine Anmeldung verwalten:\n{edit_url}\n\nST. PRIDE"
        ),
        delivery_mode=AUTOMATIC,
    ),
    TemplateDefinition(
        key="email_changed_verification",
        name="Geänderte E-Mail-Adresse bestätigen",
        trigger_description="Nach Änderung der E-Mail-Adresse einer Anmeldung.",
        subject="Neue E-Mail-Adresse für {event_name} bestätigen",
        body=(
            "Hallo {first_name},\n\n"
            "bitte bestätige deine neue E-Mail-Adresse für {event_name}:\n"
            "{verification_url}\n\n"
            "Dein bestätigter Anmelde- und Schichtstatus bleibt unverändert."
            "\n\nST. PRIDE"
        ),
        delivery_mode=AUTOMATIC,
    ),
    TemplateDefinition(
        key="registration_updated",
        name="Anmeldung geändert",
        trigger_description="Nach einer Änderung von Kontaktdaten oder Schichten.",
        subject="Deine Anmeldung für {event_name} wurde aktualisiert",
        body=(
            "Hallo {first_name},\n\n"
            "deine Kontaktdaten oder Schichten für {event_name} wurden "
            "aktualisiert.\n\nST. PRIDE"
        ),
        delivery_mode=MANUAL,
    ),
    TemplateDefinition(
        key="registration_cancellation",
        name="Schicht storniert",
        trigger_description="Wenn eine ehrenamtliche Person eine Schicht storniert.",
        subject="Schicht bei {event_name} storniert",
        body=(
            "Hallo {first_name},\n\n"
            "deine Schicht „{shift_title}“ bei {event_name} wurde storniert."
            "\n\nST. PRIDE"
        ),
        delivery_mode=MANUAL,
    ),
    TemplateDefinition(
        key="registration_rejected",
        name="Anmeldung abgelehnt",
        trigger_description=(
            "Wenn die Administration eine Person ablehnt; aktive Zuteilungen "
            "werden gleichzeitig aufgehoben."
        ),
        subject="Rückmeldung zu deiner Anmeldung für {event_name}",
        body=(
            "Hallo {first_name},\n\n"
            "leider können wir deine Anmeldung für {event_name} nicht annehmen.\n"
            "Begründung: {reason}\n\nBei Rückfragen: {contact_email}\n\nST. PRIDE"
        ),
        delivery_mode=AUTOMATIC,
    ),
    TemplateDefinition(
        key="waitlist_promoted",
        name="Von Warteliste nachgerückt",
        trigger_description=(
            "Wenn die Administration die erste wartende Person auf einen freien "
            "Schichtplatz nachrücken lässt."
        ),
        subject="Freier Platz bei {event_name}",
        body=(
            "Hallo {first_name},\n\n"
            "du bist für die Schicht „{shift_title}“ bei {event_name} von der "
            "Warteliste nachgerückt und nun bestätigt.\n\nST. PRIDE"
        ),
        delivery_mode=AUTOMATIC,
    ),
)


def ensure_mail_templates(db: Session) -> list[MailTemplate]:
    templates = {item.key: item for item in db.scalars(select(MailTemplate))}
    changed = False
    for definition in DEFAULT_TEMPLATES:
        if definition.key in templates:
            continue
        template = MailTemplate(
            key=definition.key,
            name=definition.name,
            trigger_description=definition.trigger_description,
            subject_template=definition.subject,
            body_template=definition.body,
            delivery_mode=definition.delivery_mode,
        )
        db.add(template)
        templates[definition.key] = template
        changed = True
    if changed:
        db.flush()
    return [templates[item.key] for item in DEFAULT_TEMPLATES]


def get_mail_template(db: Session, key: str) -> MailTemplate | None:
    ensure_mail_templates(db)
    return db.scalar(select(MailTemplate).where(MailTemplate.key == key))


def render_template(value: str, context: dict[str, str]) -> str:
    return PLACEHOLDER_PATTERN.sub(
        lambda match: context.get(match.group(1), match.group(0)), value
    )


def unknown_placeholders(value: str) -> set[str]:
    return set(PLACEHOLDER_PATTERN.findall(value)) - ALLOWED_PLACEHOLDERS


def queue_templated_mail(
    db: Session,
    volunteer: Volunteer,
    key: str,
    context: dict[str, str] | None = None,
) -> OutboxMessage | None:
    template = get_mail_template(db, key)
    if template is None or template.delivery_mode == DISABLED:
        return None
    values = {
        "first_name": volunteer.first_name,
        "last_name": volunteer.last_name,
        "event_name": volunteer.event.name,
        "contact_email": volunteer.event.contact_email or "volunteer@stpride.at",
        **(context or {}),
    }
    message = OutboxMessage(
        volunteer_id=volunteer.id,
        kind=key,
        subject=render_template(template.subject_template, values).replace("\n", " "),
        body=render_template(template.body_template, values),
        recipient_email=volunteer.email,
        delivery_mode=template.delivery_mode,
    )
    db.add(message)
    return message
