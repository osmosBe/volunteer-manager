"""Safe parsing helpers for optional GET filter values.

HTML GET forms submit empty strings for unselected controls. Route parameters
therefore stay as strings until this layer turns blank values into ``None`` and
returns useful, field-specific errors for malformed non-empty values.
"""

from __future__ import annotations

from datetime import date, time


class QueryFilterError(ValueError):
    """A controlled, user-facing query-filter validation error."""

    def __init__(self, field_name: str, message: str):
        super().__init__(message)
        self.field_name = field_name


def _clean(value: str | None) -> str:
    return value.strip() if value else ""


def parse_optional_id(value: str | None, *, field_name: str, label: str) -> int | None:
    """Parse a positive integer identifier while accepting an empty filter."""

    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise QueryFilterError(field_name, f"{label} ist ungültig.") from exc
    if parsed <= 0:
        raise QueryFilterError(field_name, f"{label} ist ungültig.")
    return parsed


def parse_optional_date(
    value: str | None, *, field_name: str, label: str
) -> date | None:
    """Parse an ISO date while accepting an empty filter."""

    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise QueryFilterError(field_name, f"{label} ist ungültig.") from exc


def parse_optional_time(
    value: str | None, *, field_name: str, label: str
) -> time | None:
    """Parse an ISO time while accepting an empty filter."""

    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        parsed = time.fromisoformat(cleaned)
    except ValueError as exc:
        raise QueryFilterError(field_name, f"{label} ist ungültig.") from exc
    if parsed.tzinfo is not None:
        raise QueryFilterError(field_name, f"{label} ist ungültig.")
    return parsed


def parse_checkbox(value: str | None, *, field_name: str, label: str) -> bool:
    """Parse a GET checkbox without relying on framework coercion."""

    cleaned = _clean(value).lower()
    if not cleaned or cleaned in {"0", "false", "off", "no"}:
        return False
    if cleaned in {"1", "true", "on", "yes"}:
        return True
    raise QueryFilterError(field_name, f"{label} ist ungültig.")
