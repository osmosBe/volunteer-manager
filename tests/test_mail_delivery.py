from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.main import app
from app.models import (
    AgeGroup,
    Event,
    EventStatus,
    MailTemplate,
    OutboxMessage,
    SMTPConfiguration,
    Volunteer,
)
from app.services.mail_delivery import (
    send_outbox_message,
    send_pending_automatic_messages,
)
from app.services.mail_templates import queue_templated_mail
from app.services.volunteers import deterministic_email_hash


class FakeSMTP:
    sent_messages = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def starttls(self):
        return None

    def login(self, username, password):
        assert username == "mailer"
        assert password == "secret"

    def send_message(self, message):
        self.sent_messages.append(message)


def build_mail_database(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'mail.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def add_message(db):
    event = Event(name="Mail Test", slug="mail-test", status=EventStatus.draft)
    email = "recipient@example.org"
    volunteer = Volunteer(
        event=event,
        first_name="Mail",
        last_name="Test",
        email=email,
        email_normalized=email,
        email_hash=deterministic_email_hash(email),
        age_group=AgeGroup.adult,
    )
    message = OutboxMessage(
        volunteer_id=0,
        kind="test",
        subject="Testnachricht",
        body="Hallo",
        recipient_email=email,
    )
    db.add_all([event, volunteer])
    db.flush()
    message.volunteer_id = volunteer.id
    db.add(message)
    db.commit()
    return message


def test_smtp_configuration_is_admin_managed_without_password_field(
    monkeypatch, tmp_path
):
    Session = build_mail_database(tmp_path)
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    get_settings.cache_clear()

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/admin/einstellungen/smtp",
            data={
                "host": "smtp.example.org",
                "port": 587,
                "username": "mailer",
                "from_email": "volunteer@example.org",
                "from_name": "ST. PRIDE",
                "use_starttls": "true",
                "enabled": "true",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        with Session() as db:
            configuration = db.query(SMTPConfiguration).one()
            assert configuration.host == "smtp.example.org"
            assert configuration.enabled is True
            assert not hasattr(configuration, "password")
    finally:
        app.dependency_overrides.clear()
        monkeypatch.delenv("SMTP_PASSWORD")
        get_settings.cache_clear()


def test_outbox_delivery_uses_environment_password(monkeypatch, tmp_path):
    Session = build_mail_database(tmp_path)
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.mail_delivery.smtplib.SMTP", FakeSMTP)
    FakeSMTP.sent_messages.clear()
    with Session() as db:
        db.add(
            SMTPConfiguration(
                host="smtp.example.org",
                port=587,
                username="mailer",
                from_email="volunteer@example.org",
                from_name="ST. PRIDE",
                use_starttls=True,
                enabled=True,
            )
        )
        message = add_message(db)
        send_outbox_message(db, message)
        assert message.sent_at is not None
        assert len(FakeSMTP.sent_messages) == 1
        assert FakeSMTP.sent_messages[0]["To"] == "recipient@example.org"
    monkeypatch.delenv("SMTP_PASSWORD")
    get_settings.cache_clear()


def test_enabled_smtp_automatically_sends_pending_verification(monkeypatch, tmp_path):
    Session = build_mail_database(tmp_path)
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.mail_delivery.smtplib.SMTP", FakeSMTP)
    FakeSMTP.sent_messages.clear()
    with Session() as db:
        db.add(
            SMTPConfiguration(
                host="smtp.example.org",
                port=587,
                username="mailer",
                from_email="volunteer@example.org",
                from_name="ST. PRIDE",
                use_starttls=True,
                enabled=True,
            )
        )
        message = add_message(db)
        message.kind = "email_verification"
        db.commit()

        message.delivery_mode = "automatic"
        db.commit()
        send_pending_automatic_messages(db, message.volunteer_id)

        db.refresh(message)
        assert message.sent_at is not None
        assert len(FakeSMTP.sent_messages) == 1
    monkeypatch.delenv("SMTP_PASSWORD")
    get_settings.cache_clear()


def test_admin_can_edit_template_and_disable_an_outbox_flow(tmp_path):
    Session = build_mail_database(tmp_path)

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        page = client.get("/admin/einstellungen/mail-templates")
        assert page.status_code == 200
        assert "Mailvorlagen" in page.text
        assert "Nach einer öffentlichen Anmeldung" in page.text
        response = client.post(
            "/admin/einstellungen/mail-templates/registration_updated",
            data={
                "subject_template": "Update für {event_name}",
                "body_template": "Hallo {first_name}, das ist der neue Text.",
                "delivery_mode": "disabled",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        with Session() as db:
            template = (
                db.query(MailTemplate).filter_by(key="registration_updated").one()
            )
            assert template.delivery_mode == "disabled"
            message = add_message(db)
            volunteer = db.get(Volunteer, message.volunteer_id)
            assert queue_templated_mail(db, volunteer, "registration_updated") is None
    finally:
        app.dependency_overrides.clear()
