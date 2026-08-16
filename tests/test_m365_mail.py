import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.admin_mail import require_mail_service
from app.config.settings import Settings, get_settings
from app.database.base import Base
from app.database.session import create_database_engine, get_db
from app.mail.models import MailAttachment, MailMessage, MailRecipient
from app.mail.providers import ConsoleMailProvider, MicrosoftGraphMailProvider
from app.mail.service import MailService, mail_diagnostics
from app.mail.tokens import GraphAuthenticationError, GraphTokenProvider
from app.main import app
from app.models import AuditLog


class StaticTokenProvider(GraphTokenProvider):
    def get_access_token(self) -> str:
        return "canary-access-token"


class FailingTokenProvider(GraphTokenProvider):
    def get_access_token(self) -> str:
        raise GraphAuthenticationError("safe")


def message(**updates):
    values = {
        "to": [MailRecipient(email="to@example.org", name="To Person")],
        "subject": "Mail test",
        "text_body": "Plain body",
    }
    values.update(updates)
    return MailMessage(**values)


def graph_provider(handler, *, sleeps=None, max_attempts=3):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return MicrosoftGraphMailProvider(
        StaticTokenProvider(),
        client=client,
        max_attempts=max_attempts,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


def test_console_provider_logs_metadata_without_body_or_attachments(caplog):
    provider = ConsoleMailProvider()
    mail = message(
        text_body="sensitive-token-123",
        attachments=[
            MailAttachment(
                filename="private.txt",
                content_type="text/plain",
                content=b"attachment-secret",
            )
        ],
    )

    with caplog.at_level("INFO", logger="app.mail"):
        result = provider.send(mail, sender=MailRecipient(email="sender@example.org"))

    assert result.success is True
    output = caplog.text
    assert '"provider": "console"' in output
    assert "to@example.org" in output
    assert "Mail test" in output
    assert "sensitive-token-123" not in output
    assert "attachment-secret" not in output


def test_console_provider_never_performs_external_http(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda *args, **kwargs: pytest.fail("unexpected HTTP call"),
    )
    result = ConsoleMailProvider().send(
        message(), sender=MailRecipient(email="sender@example.org")
    )
    assert result.success


def test_graph_request_uses_shared_mailbox_and_serializes_all_fields():
    captured = {}

    def handler(request):
        captured["request"] = request
        captured["json"] = json.loads(request.content)
        return httpx.Response(202, headers={"request-id": "request-123"})

    mail = message(
        cc=[MailRecipient(email="cc@example.org")],
        bcc=[MailRecipient(email="bcc@example.org", name="BCC Person")],
        html_body="<p>Safe HTML</p>",
        reply_to=[MailRecipient(email="reply@example.org")],
        attachments=[
            MailAttachment(
                filename="test.txt", content_type="text/plain", content=b"hello"
            )
        ],
    )
    result = graph_provider(handler).send(
        mail,
        sender=MailRecipient(email="shared-mailbox@example.org", name="Shared"),
    )

    assert result.success
    assert result.correlation_id == "request-123"
    request = captured["request"]
    assert request.url.path == "/v1.0/users/shared-mailbox@example.org/sendMail"
    assert "/me/sendMail" not in str(request.url)
    assert request.headers["authorization"] == "Bearer canary-access-token"
    payload = captured["json"]
    graph_message = payload["message"]
    assert payload["saveToSentItems"] is True
    assert graph_message["toRecipients"][0]["emailAddress"] == {
        "address": "to@example.org",
        "name": "To Person",
    }
    assert graph_message["ccRecipients"][0]["emailAddress"]["address"] == (
        "cc@example.org"
    )
    assert graph_message["bccRecipients"][0]["emailAddress"]["address"] == (
        "bcc@example.org"
    )
    assert graph_message["from"]["emailAddress"] == {
        "address": "shared-mailbox@example.org",
        "name": "Shared",
    }
    assert graph_message["body"] == {
        "contentType": "HTML",
        "content": "<p>Safe HTML</p>",
    }
    assert graph_message["replyTo"][0]["emailAddress"]["address"] == (
        "reply@example.org"
    )
    assert graph_message["attachments"][0] == {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "test.txt",
        "contentType": "text/plain",
        "contentBytes": base64.b64encode(b"hello").decode("ascii"),
    }


def test_graph_uses_text_body_when_html_is_absent():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(202)

    graph_provider(handler).send(
        message(), sender=MailRecipient(email="sender@example.org")
    )
    assert captured["message"]["body"] == {
        "contentType": "Text",
        "content": "Plain body",
    }


def test_graph_authentication_failure_does_not_call_http():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("HTTP must not run without a token")
        )
    )
    provider = MicrosoftGraphMailProvider(FailingTokenProvider(), client=client)
    result = provider.send(message(), sender=MailRecipient(email="sender@example.org"))
    assert not result.success
    assert result.error_code == "authentication_failed"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, "invalid_recipient"),
        (401, "authentication_failed"),
        (403, "authorization_failed"),
        (404, "mailbox_not_found"),
    ],
)
def test_graph_permanent_errors_are_not_retried(status_code, expected):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status_code)

    result = graph_provider(handler).send(
        message(), sender=MailRecipient(email="sender@example.org")
    )
    assert calls == 1
    assert result.error_code == expected


def test_graph_429_respects_retry_after_then_succeeds():
    calls = 0
    sleeps = []

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(202)

    result = graph_provider(handler, sleeps=sleeps).send(
        message(), sender=MailRecipient(email="sender@example.org")
    )
    assert result.success
    assert calls == 2
    assert sleeps == [7.0]


def test_graph_5xx_retries_are_bounded():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    result = graph_provider(handler, max_attempts=3).send(
        message(), sender=MailRecipient(email="sender@example.org")
    )
    assert calls == 3
    assert result.error_code == "provider_unavailable"


def test_graph_network_timeout_retries_are_bounded():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    result = graph_provider(handler, max_attempts=2).send(
        message(), sender=MailRecipient(email="sender@example.org")
    )
    assert calls == 2
    assert result.error_code == "network_timeout"


def test_mail_service_applies_configured_reply_to():
    captured = {}

    class CapturingProvider(ConsoleMailProvider):
        def send(self, message, *, sender):
            captured["message"] = message
            return super().send(message, sender=sender)

    service = MailService(
        CapturingProvider(),
        sender=MailRecipient(email="sender@example.org"),
        default_reply_to=MailRecipient(email="reply@example.org"),
    )
    service.send(message())
    assert str(captured["message"].reply_to[0].email) == "reply@example.org"


def test_mail_service_rejects_oversized_attachments():
    service = MailService(
        ConsoleMailProvider(), sender=MailRecipient(email="sender@example.org")
    )
    oversized = message(
        attachments=[
            MailAttachment(
                filename="large.bin",
                content_type="application/octet-stream",
                content=b"x" * (3 * 1024 * 1024 + 1),
            )
        ]
    )
    with pytest.raises(ValueError, match="3 MiB"):
        service.send(oversized)


def test_mail_diagnostics_never_exposes_secrets_or_tokens():
    secret = "canary-client-secret"
    diagnostics = mail_diagnostics(
        Settings(
            mail_provider="graph",
            mail_from_address="sender@example.org",
            m365_tenant_id="tenant-id",
            m365_client_id="client-id",
            m365_client_secret=secret,
        )
    )
    serialized = json.dumps(diagnostics)
    assert diagnostics["configured"] is True
    assert diagnostics["credential_configured"] is True
    assert secret not in serialized
    assert "access_token" not in serialized


def test_admin_mail_routes_require_admin_permission(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        assert client.get("/admin/mail").status_code == 401
        assert (
            client.post(
                "/admin/mail/test", data={"recipient": "test@example.org"}
            ).status_code
            == 401
        )
        principal = base64.b64encode(
            json.dumps(
                {
                    "claims": [
                        {"typ": "roles", "val": "Volunteer.Manager"},
                        {"typ": "email", "val": "manager@example.org"},
                    ]
                }
            ).encode()
        ).decode()
        headers = {"x-ms-client-principal": principal}
        assert client.get("/admin/mail", headers=headers).status_code == 403
        assert (
            client.post(
                "/admin/mail/test",
                headers=headers,
                data={"recipient": "test@example.org"},
            ).status_code
            == 403
        )
    finally:
        monkeypatch.delenv("AUTH_MODE")
        get_settings.cache_clear()


def test_debug_mail_is_404_when_debug_is_false(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    try:
        assert TestClient(app).get("/debug/mail").status_code == 404
    finally:
        monkeypatch.delenv("DEBUG")
        get_settings.cache_clear()


def test_debug_mail_reports_only_safe_credential_state(monkeypatch):
    secret = "debug-canary-secret"
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("MAIL_PROVIDER", "graph")
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "sender@example.org")
    monkeypatch.setenv("M365_TENANT_ID", "tenant")
    monkeypatch.setenv("M365_CLIENT_ID", "client")
    monkeypatch.setenv("M365_CLIENT_SECRET", secret)
    get_settings.cache_clear()
    try:
        response = TestClient(app).get("/debug/mail")
        assert response.status_code == 200
        assert response.json()["credential_configured"] is True
        assert secret not in response.text
    finally:
        for name in (
            "DEBUG",
            "MAIL_PROVIDER",
            "MAIL_FROM_ADDRESS",
            "M365_TENANT_ID",
            "M365_CLIENT_ID",
            "M365_CLIENT_SECRET",
        ):
            monkeypatch.delenv(name)
        get_settings.cache_clear()


def test_admin_test_mail_sends_and_writes_minimal_audit(monkeypatch, tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'admin-mail.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    service = MailService(
        ConsoleMailProvider(), sender=MailRecipient(email="sender@example.org")
    )

    def override_get_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_mail_service] = lambda: service
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "sender@example.org")
    monkeypatch.setenv("ENVIRONMENT", "test")
    get_settings.cache_clear()
    try:
        response = TestClient(app).post(
            "/admin/mail/test", data={"recipient": "explicit@example.org"}
        )
        assert response.status_code == 200
        assert "Test email accepted by console" in response.text
        with Session() as db:
            entry = db.query(AuditLog).filter_by(action="mail.test").one()
            metadata = json.loads(entry.metadata_json)
            assert metadata == {
                "provider": "console",
                "recipient_count": 1,
                "success": True,
                "error_code": None,
            }
            assert "explicit@example.org" not in entry.metadata_json
    finally:
        app.dependency_overrides.clear()
        monkeypatch.delenv("MAIL_FROM_ADDRESS")
        monkeypatch.delenv("ENVIRONMENT")
        get_settings.cache_clear()
