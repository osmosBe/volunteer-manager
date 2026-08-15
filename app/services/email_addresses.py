"""Consistent server-side validation for volunteer and SMTP addresses."""

import re

from email_validator import EmailNotValidError, validate_email

from app.services.volunteers import normalize_email

DEMO_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@example\.invalid$")


class EmailAddressError(ValueError):
    pass


def validate_email_address(value: str) -> str:
    raw = value.strip()
    if DEMO_EMAIL_PATTERN.fullmatch(raw):
        return normalize_email(raw)
    try:
        result = validate_email(raw, check_deliverability=False)
    except EmailNotValidError as exc:
        raise EmailAddressError("Bitte gib eine gültige E-Mail-Adresse ein.") from exc
    return normalize_email(result.normalized)
