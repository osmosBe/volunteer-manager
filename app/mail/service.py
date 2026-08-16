"""Provider-neutral transactional mail service and composition root."""

from functools import lru_cache

from app.config.settings import Settings, get_settings
from app.mail.models import MailMessage, MailRecipient, MailSendResult
from app.mail.providers import (
    ConsoleMailProvider,
    MailProvider,
    MicrosoftGraphMailProvider,
)
from app.mail.tokens import (
    ClientSecretGraphTokenProvider,
    ManagedIdentityGraphTokenProvider,
)
from app.services.email_addresses import EmailAddressError, validate_email_address


class MailConfigurationError(RuntimeError):
    pass


class MailService:
    def __init__(
        self,
        provider: MailProvider,
        *,
        sender: MailRecipient,
        default_reply_to: MailRecipient | None = None,
    ):
        self.provider = provider
        self.sender = sender
        self.default_reply_to = default_reply_to

    def send(self, message: MailMessage) -> MailSendResult:
        # Pydantic validates all recipient addresses. Re-validation here keeps
        # the service boundary safe even if models are constructed unusually.
        for recipient in [*message.to, *message.cc, *message.bcc]:
            try:
                validate_email_address(str(recipient.email))
            except EmailAddressError as exc:
                raise ValueError("Invalid mail recipient.") from exc
        attachment_bytes = sum(len(item.content) for item in message.attachments)
        if attachment_bytes > 3 * 1024 * 1024:
            raise ValueError("Attachments exceed the 3 MiB transactional limit.")
        if not message.reply_to and self.default_reply_to:
            message = message.model_copy(
                update={"reply_to": [self.default_reply_to]}, deep=True
            )
        return self.provider.send(message, sender=self.sender)


def mail_diagnostics(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    sender = settings.mail_from_address
    graph_configured = bool(
        settings.m365_tenant_id
        and settings.m365_client_id
        and (
            settings.m365_auth_mode == "managed_identity"
            or (
                settings.m365_client_secret
                and settings.m365_client_secret.get_secret_value()
            )
        )
    )
    configured = bool(sender) and (
        settings.mail_provider == "console" or graph_configured
    )
    return {
        "provider": settings.mail_provider,
        "configured": configured,
        "sender": sender,
        "auth_mode": settings.m365_auth_mode,
        "tenant_configured": bool(settings.m365_tenant_id),
        "client_id_configured": bool(settings.m365_client_id),
        "credential_configured": bool(
            settings.m365_auth_mode == "managed_identity"
            or (
                settings.m365_client_secret
                and settings.m365_client_secret.get_secret_value()
            )
        ),
    }


def build_mail_service(settings: Settings | None = None) -> MailService:
    settings = settings or get_settings()
    if not settings.mail_from_address:
        raise MailConfigurationError("MAIL_FROM_ADDRESS is not configured.")
    try:
        sender = MailRecipient(
            email=settings.mail_from_address, name=settings.mail_from_name
        )
        reply_to = (
            MailRecipient(email=settings.mail_reply_to)
            if settings.mail_reply_to
            else None
        )
    except ValueError as exc:
        raise MailConfigurationError("Mail sender configuration is invalid.") from exc

    if settings.mail_provider == "console":
        provider: MailProvider = ConsoleMailProvider()
    elif settings.mail_provider == "graph":
        if not settings.m365_tenant_id or not settings.m365_client_id:
            raise MailConfigurationError("Microsoft Graph is not fully configured.")
        if settings.m365_auth_mode == "client_secret":
            client_secret = (
                settings.m365_client_secret.get_secret_value()
                if settings.m365_client_secret
                else ""
            )
            if not client_secret:
                raise MailConfigurationError(
                    "M365_CLIENT_SECRET is required for client_secret mode."
                )
            token_provider = ClientSecretGraphTokenProvider(
                settings.m365_tenant_id,
                settings.m365_client_id,
                client_secret,
            )
        else:
            token_provider = ManagedIdentityGraphTokenProvider()
        provider = MicrosoftGraphMailProvider(token_provider)
    else:  # pragma: no cover - Settings rejects unsupported providers.
        raise MailConfigurationError("Unsupported mail provider.")
    return MailService(provider, sender=sender, default_reply_to=reply_to)


@lru_cache
def get_mail_service() -> MailService:
    return build_mail_service()
