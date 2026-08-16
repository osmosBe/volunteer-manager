import base64
import binascii
import json
from collections.abc import Iterable
from typing import Any

from app.auth.models import AuthenticatedUser

EASYAUTH_PRINCIPAL_HEADER = "x-ms-client-principal"
EASYAUTH_PRINCIPAL_NAME_HEADER = "x-ms-client-principal-name"
EASYAUTH_PRINCIPAL_ID_HEADER = "x-ms-client-principal-id"

EMAIL_CLAIM_TYPES = {
    "preferred_username",
    "email",
    "emails",
    "upn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
}
NAME_CLAIM_TYPES = {
    "name",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
}
ID_CLAIM_TYPES = {
    "oid",
    "sub",
    "http://schemas.microsoft.com/identity/claims/objectidentifier",
}
ROLE_CLAIM_TYPES = {
    "roles",
    "role",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
}
GROUP_CLAIM_TYPES = {
    "groups",
    "group",
    "groupsid",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/groupsid",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
    "http://schemas.xmlsoap.org/claims/Group",
}


def _clean(value: Any) -> str:
    return str(value).strip()


def _dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _clean(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _claim_name(claim: dict[str, Any]) -> str:
    return _clean(claim.get("typ") or claim.get("type") or "")


def _claim_value(claim: dict[str, Any]) -> Any:
    return claim.get("val") if "val" in claim else claim.get("value")


def _claim_values(claims: list[dict[str, Any]], claim_types: set[str]) -> list[str]:
    values: list[Any] = []
    for claim in claims:
        if _claim_name(claim) in claim_types:
            value = _claim_value(claim)
            if isinstance(value, list):
                values.extend(value)
            else:
                values.append(value)
    return _dedupe(values)


def _decode_principal(encoded_principal: str) -> dict[str, Any] | None:
    try:
        padded = encoded_principal + "=" * (-len(encoded_principal) % 4)
        decoded = base64.b64decode(padded, validate=True)
        principal = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return principal if isinstance(principal, dict) else None


def parse_easyauth_principal(headers: Any) -> AuthenticatedUser | None:
    """Parse Azure EasyAuth headers into a sanitized user object."""

    encoded_principal = headers.get(EASYAUTH_PRINCIPAL_HEADER)
    principal = _decode_principal(encoded_principal) if encoded_principal else None
    claims = principal.get("claims", []) if principal else []
    claims = claims if isinstance(claims, list) else []
    claims = [claim for claim in claims if isinstance(claim, dict)]

    email = next(iter(_claim_values(claims, EMAIL_CLAIM_TYPES)), None)
    name = next(iter(_claim_values(claims, NAME_CLAIM_TYPES)), None)
    user_id = next(iter(_claim_values(claims, ID_CLAIM_TYPES)), None)

    header_name = _clean(headers.get(EASYAUTH_PRINCIPAL_NAME_HEADER) or "")
    header_id = _clean(headers.get(EASYAUTH_PRINCIPAL_ID_HEADER) or "")
    email = email or header_name or None
    name = name or email or ""
    user_id = user_id or header_id or ""

    roles = _claim_values(claims, ROLE_CLAIM_TYPES)
    groups = _claim_values(claims, GROUP_CLAIM_TYPES)
    claim_names = _dedupe(_claim_name(claim) for claim in claims)

    if not any([principal, email, user_id]):
        return None

    return AuthenticatedUser(
        user_id=user_id,
        name=name,
        email=email,
        roles=roles,
        groups=groups,
        claims=claim_names,
    )
