"""SMTP delivery adapter. Passwords remain environment-managed secrets."""

import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models import OutboxMessage, SMTPConfiguration, Volunteer
from app.models.core import utcnow
from app.services.mail_templates import AUTOMATIC


class MailDeliveryError(RuntimeError):
    pass


def get_smtp_configuration(db: Session) -> SMTPConfiguration | None:
    return db.scalar(select(SMTPConfiguration).order_by(SMTPConfiguration.id).limit(1))


def smtp_password_configured() -> bool:
    secret = get_settings().smtp_password
    return bool(secret and secret.get_secret_value())


def send_outbox_message(db: Session, message: OutboxMessage) -> None:
    configuration = get_smtp_configuration(db)
    if configuration is None or not configuration.enabled:
        raise MailDeliveryError("SMTP ist nicht aktiviert.")
    if configuration.use_ssl and configuration.use_starttls:
        raise MailDeliveryError(
            "SSL und STARTTLS dürfen nicht gleichzeitig aktiv sein."
        )
    recipient = message.recipient_email
    if not recipient:
        volunteer = db.get(Volunteer, message.volunteer_id)
        recipient = volunteer.email if volunteer else None
    if not recipient:
        raise MailDeliveryError("Für diese Nachricht fehlt eine Empfängeradresse.")
    password_secret = get_settings().smtp_password
    password = password_secret.get_secret_value() if password_secret else None
    if configuration.username and not password:
        raise MailDeliveryError(
            "SMTP_PASSWORD ist nicht als Environment-Secret gesetzt."
        )

    email = EmailMessage()
    email["From"] = f"{configuration.from_name} <{configuration.from_email}>"
    email["To"] = recipient
    email["Subject"] = message.subject
    email.set_content(message.body)

    smtp_class = smtplib.SMTP_SSL if configuration.use_ssl else smtplib.SMTP
    try:
        with smtp_class(configuration.host, configuration.port, timeout=15) as server:
            if configuration.use_starttls:
                server.starttls()
            if configuration.username:
                server.login(configuration.username, password or "")
            server.send_message(email)
    except (OSError, smtplib.SMTPException) as exc:
        message.last_error = type(exc).__name__
        db.commit()
        raise MailDeliveryError("SMTP-Versand fehlgeschlagen.") from exc

    message.sent_at = utcnow()
    message.last_error = None
    db.commit()


def send_pending_automatic_messages(db: Session, volunteer_id: int) -> None:
    """Best-effort delivery for messages configured as automatic."""
    configuration = get_smtp_configuration(db)
    if configuration is None or not configuration.enabled:
        return
    messages = list(
        db.scalars(
            select(OutboxMessage)
            .where(
                OutboxMessage.volunteer_id == volunteer_id,
                OutboxMessage.delivery_mode == AUTOMATIC,
                OutboxMessage.sent_at.is_(None),
            )
            .order_by(OutboxMessage.id)
        )
    )
    for message in messages:
        try:
            send_outbox_message(db, message)
        except MailDeliveryError:
            # Registration/admin state remains committed; admins can retry.
            continue
