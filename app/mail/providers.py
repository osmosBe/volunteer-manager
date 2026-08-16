"""Transactional mail providers."""

import base64
import json
import logging
import time
from abc import ABC, abstractmethod
from urllib.parse import quote

import httpx

from app.mail.models import MailMessage, MailRecipient, MailSendResult
from app.mail.tokens import GraphAuthenticationError, GraphTokenProvider

logger = logging.getLogger("app.mail")


class MailProvider(ABC):
    name: str

    @abstractmethod
    def send(self, message: MailMessage, *, sender: MailRecipient) -> MailSendResult:
        raise NotImplementedError


def _structured_log(**values: object) -> None:
    logger.info(json.dumps(values, sort_keys=True, default=str))


class ConsoleMailProvider(MailProvider):
    name = "console"

    def send(self, message: MailMessage, *, sender: MailRecipient) -> MailSendResult:
        recipients = [
            str(item.email) for item in [*message.to, *message.cc, *message.bcc]
        ]
        _structured_log(
            provider=self.name,
            operation="send",
            success=True,
            recipient_count=len(recipients),
            recipients=recipients,
            subject=message.subject,
            status_category="local",
        )
        return MailSendResult(success=True, provider=self.name)


class MicrosoftGraphMailProvider(MailProvider):
    name = "graph"
    transient_statuses = {429, 500, 502, 503, 504}

    def __init__(
        self,
        token_provider: GraphTokenProvider,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 3,
        sleep=time.sleep,
    ):
        self._token_provider = token_provider
        self._client = client or httpx.Client(timeout=15.0)
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep

    @staticmethod
    def _recipient(recipient: MailRecipient) -> dict[str, object]:
        address = {"address": str(recipient.email)}
        if recipient.name:
            address["name"] = recipient.name
        return {"emailAddress": address}

    def _payload(
        self, message: MailMessage, sender: MailRecipient
    ) -> dict[str, object]:
        use_html = message.html_body is not None
        graph_message: dict[str, object] = {
            "subject": message.subject,
            "body": {
                "contentType": "HTML" if use_html else "Text",
                "content": message.html_body if use_html else message.text_body,
            },
            "toRecipients": [self._recipient(item) for item in message.to],
            "ccRecipients": [self._recipient(item) for item in message.cc],
            "bccRecipients": [self._recipient(item) for item in message.bcc],
            "from": self._recipient(sender),
        }
        if message.reply_to:
            graph_message["replyTo"] = [
                self._recipient(item) for item in message.reply_to
            ]
        if message.attachments:
            graph_message["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": item.filename,
                    "contentType": item.content_type,
                    "contentBytes": base64.b64encode(item.content).decode("ascii"),
                }
                for item in message.attachments
            ]
        return {"message": graph_message, "saveToSentItems": True}

    @staticmethod
    def _error_code(status_code: int) -> str:
        if status_code == 401:
            return "authentication_failed"
        if status_code == 403:
            return "authorization_failed"
        if status_code == 404:
            return "mailbox_not_found"
        if status_code == 429:
            return "throttled"
        if status_code == 400:
            return "invalid_recipient"
        if status_code >= 500:
            return "provider_unavailable"
        return "invalid_request"

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After", "").strip()
        if value.isdigit():
            return min(float(value), 30.0)
        return min(float(2 ** (attempt - 1)), 8.0)

    def send(self, message: MailMessage, *, sender: MailRecipient) -> MailSendResult:
        recipient_count = len(message.to) + len(message.cc) + len(message.bcc)
        try:
            token = self._token_provider.get_access_token()
        except GraphAuthenticationError:
            _structured_log(
                provider=self.name,
                operation="send",
                success=False,
                recipient_count=recipient_count,
                status_category="authentication",
            )
            return MailSendResult(
                success=False,
                provider=self.name,
                error_code="authentication_failed",
            )

        endpoint = (
            "https://graph.microsoft.com/v1.0/users/"
            f"{quote(str(sender.email), safe='@')}/sendMail"
        )
        headers = {"Authorization": f"Bearer {token}"}
        payload = self._payload(message, sender)
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(endpoint, headers=headers, json=payload)
            except httpx.TimeoutException:
                error_code = "network_timeout"
                if attempt < self._max_attempts:
                    self._sleep(min(float(2 ** (attempt - 1)), 8.0))
                    continue
                break
            except httpx.RequestError:
                error_code = "network_failure"
                if attempt < self._max_attempts:
                    self._sleep(min(float(2 ** (attempt - 1)), 8.0))
                    continue
                break

            correlation_id = response.headers.get("request-id") or response.headers.get(
                "client-request-id"
            )
            if response.status_code in {200, 202}:
                _structured_log(
                    provider=self.name,
                    operation="send",
                    success=True,
                    recipient_count=recipient_count,
                    status_category="success",
                    correlation_id=correlation_id,
                )
                return MailSendResult(
                    success=True,
                    provider=self.name,
                    correlation_id=correlation_id,
                )
            error_code = self._error_code(response.status_code)
            if (
                response.status_code in self.transient_statuses
                and attempt < self._max_attempts
            ):
                self._sleep(self._retry_delay(response, attempt))
                continue
            break

        _structured_log(
            provider=self.name,
            operation="send",
            success=False,
            recipient_count=recipient_count,
            status_category=error_code,
            correlation_id=locals().get("correlation_id"),
        )
        return MailSendResult(
            success=False,
            provider=self.name,
            error_code=error_code,
            correlation_id=locals().get("correlation_id"),
        )
