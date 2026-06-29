import base64
import binascii
import json
from typing import Any

from app.auth.models import AuthenticatedUser

EASYAUTH_PRINCIPAL_HEADER = "x-ms-client-principal"
EASYAUTH_PRINCIPAL_NAME_HEADER = "x-ms-client-principal-name"
EASYAUTH_PRINCIPAL_ID_HEADER = "x-ms-client-principal-id"
GROUP_CLAIM_TYPES = {
    "groups",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
}
EMAIL_CLAIM_TYPES = {
    "email",
    "emails",
    "preferred_username",
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


def _claim_name(claim: dict[str, Any]) -> str:
    return str(claim.get("typ") or claim.get("type") or "")


def _claim_value(claim: dict[str, Any]) -> Any:
    return claim.get("val") if "val" in claim else claim.get("value")


def _claim_values(claims: list[dict[str, Any]], claim_types: set[str]) -> list[str]:
    values: list[str] = []
    for claim in claims:
        value = _claim_value(claim)
        if _claim_name(claim) in claim_types and value:
            values.append(str(value))
    return values


def _decode_principal(encoded_principal: str) -> dict[str, Any] | None:
    try:
        padded = encoded_principal + "=" * (-len(encoded_principal) % 4)
        decoded = base64.b64decode(padded, validate=True)
        principal = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(principal, dict):
        return None
    return principal


def parse_easyauth_principal(headers: Any) -> AuthenticatedUser | None:
    """Parse Azure EasyAuth headers into a minimal user object.

    WARNING: EasyAuth headers are only trustworthy when the app is deployed behind
    Azure Container Apps Authentication / Authorization and AUTH_MODE=easyauth.
    Never call this parser as proof of authentication unless that mode is enabled.
    """

    encoded_principal = headers.get(EASYAUTH_PRINCIPAL_HEADER)
    principal = _decode_principal(encoded_principal) if encoded_principal else None
    claims = principal.get("claims", []) if principal else []
    if not isinstance(claims, list):
        claims = []
    claims = [claim for claim in claims if isinstance(claim, dict)]

    header_name = headers.get(EASYAUTH_PRINCIPAL_NAME_HEADER)
    header_id = headers.get(EASYAUTH_PRINCIPAL_ID_HEADER)
    email = header_name or next(iter(_claim_values(claims, EMAIL_CLAIM_TYPES)), None)
    name = next(iter(_claim_values(claims, NAME_CLAIM_TYPES)), None) or email
    user_id = header_id or next(iter(_claim_values(claims, ID_CLAIM_TYPES)), None)
    groups = _claim_values(claims, GROUP_CLAIM_TYPES)
    claim_names = [_claim_name(claim) for claim in claims if _claim_name(claim)]

    if not any([principal, email, user_id]):
        return None

    return AuthenticatedUser(
        user_id=user_id,
        name=name,
        email=email,
        groups=groups,
        claims=claim_names,
    )
