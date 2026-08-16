"""Portable, database-backed links for privacy and imprint pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.models import LegalSettings
from app.models.core import utcnow

MAX_LEGAL_URL_LENGTH = 2048
INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class LegalSettingsError(ValueError):
    def __init__(self, field_errors: dict[str, str]):
        super().__init__("Mindestens ein Link ist ungültig.")
        self.field_errors = field_errors


@dataclass(frozen=True)
class ValidatedLegalLinks:
    privacy_url: str | None
    imprint_url: str | None


def validate_legal_url(value: str | None) -> str | None:
    """Accept HTTPS targets or explicit root-relative application paths."""

    url = str(value or "").strip()
    if not url:
        return None
    if (
        len(url) > MAX_LEGAL_URL_LENGTH
        or any(ord(character) <= 32 or ord(character) == 127 for character in url)
        or "\\" in url
        or any(encoded in url.lower() for encoded in ("%00", "%0a", "%0d"))
        or INVALID_PERCENT_ESCAPE.search(url)
    ):
        raise ValueError("Bitte gib eine gültige HTTPS-URL oder einen Pfad ab / ein.")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ValueError(
            "Bitte gib eine gültige HTTPS-URL oder einen Pfad ab / ein."
        ) from exc

    if url.startswith("/"):
        if url.startswith("//") or parsed.scheme or parsed.netloc:
            raise ValueError(
                "Interne Links müssen mit genau einem Schrägstrich beginnen."
            )
        return url

    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Externe Links müssen mit https:// beginnen.")
    if parsed.username or parsed.password:
        raise ValueError("Links dürfen keine Zugangsdaten enthalten.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Der Link enthält einen ungültigen Port.") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Der Link enthält einen ungültigen Port.")
    return url


def validate_legal_links(
    privacy_url: str | None, imprint_url: str | None
) -> ValidatedLegalLinks:
    values: dict[str, str | None] = {}
    errors: dict[str, str] = {}
    for field_name, raw_value in (
        ("privacy_url", privacy_url),
        ("imprint_url", imprint_url),
    ):
        try:
            values[field_name] = validate_legal_url(raw_value)
        except ValueError as exc:
            errors[field_name] = str(exc)
    if errors:
        raise LegalSettingsError(errors)
    return ValidatedLegalLinks(
        privacy_url=values["privacy_url"],
        imprint_url=values["imprint_url"],
    )


def legal_target_type(value: str | None) -> str:
    if not value:
        return "hidden"
    return "relative" if value.startswith("/") else "https"


class LegalSettingsRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self) -> LegalSettings | None:
        return self.db.get(LegalSettings, 1)

    def _get_or_create(self) -> LegalSettings:
        settings = self.get()
        if settings is None:
            settings = LegalSettings(id=1)
            self.db.add(settings)
        return settings

    def update(self, privacy_url: str | None, imprint_url: str | None) -> LegalSettings:
        validated = validate_legal_links(privacy_url, imprint_url)
        settings = self._get_or_create()
        settings.privacy_url = validated.privacy_url
        settings.imprint_url = validated.imprint_url
        settings.updated_at = utcnow()
        self.db.flush()
        return settings
