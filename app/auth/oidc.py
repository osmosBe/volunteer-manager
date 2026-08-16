"""Generic OpenID Connect adapter.

Only a signed, short-lived OAuth transaction and an opaque session identifier
are held in cookies. Tokens are used during the callback only and are never
returned to application code, templates, logs, or diagnostics.
"""

from __future__ import annotations

import secrets
import time
from threading import RLock
from typing import Any
from urllib.parse import urljoin

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request, status

from app.auth.models import AuthenticatedUser
from app.config.settings import Settings, get_settings

_SESSION_KEY = "oidc_session_id"
_TRANSACTION_KEY = "oidc_transaction_id"
_SESSION_TTL_SECONDS = 8 * 60 * 60
_TRANSACTION_TTL_SECONDS = 10 * 60
_MAX_SESSIONS = 10_000
_MAX_TRANSACTIONS = 2_048
_sessions: dict[str, tuple[float, AuthenticatedUser]] = {}
_transactions: dict[str, tuple[float, dict[str, Any]]] = {}
_sessions_lock = RLock()


def claim_values(claims: dict[str, Any], paths: list[str]) -> list[str]:
    """Get scalar/list claim values from safe dotted claim paths."""
    values: list[str] = []
    for path in paths:
        current: Any = claims
        for segment in path.split("."):
            if not segment or not isinstance(current, dict):
                current = None
                break
            current = current.get(segment)
        candidates = current if isinstance(current, list | tuple | set) else [current]
        for candidate in candidates:
            if isinstance(candidate, str | int | float):
                text = str(candidate).strip()
                if text:
                    values.append(text)
    return sorted(set(values))


def claim_value(claims: dict[str, Any], paths: list[str]) -> str | None:
    # Identity claim configuration expresses precedence, unlike role/group
    # aggregation where deterministic sorting is desirable.
    for path in paths:
        values = claim_values(claims, [path])
        if values:
            return values[0]
    return None


def user_from_claims(claims: dict[str, Any], settings: Settings) -> AuthenticatedUser:
    """Normalize generic OIDC claims into the provider-independent user model."""
    user_id = claim_value(claims, [settings.oidc_user_id_claim]) or ""
    email = claim_value(claims, settings.oidc_email_claims)
    name = claim_value(claims, [settings.oidc_name_claim]) or email or user_id
    return AuthenticatedUser(
        user_id=user_id,
        name=name,
        email=email,
        roles=claim_values(claims, settings.oidc_role_claims),
        groups=claim_values(claims, settings.oidc_group_claims),
        claims=sorted(str(key) for key in claims if str(key).strip()),
    )


def _oauth_client(settings: Settings):
    oauth = OAuth()
    oauth.register(
        name="oidc",
        client_id=settings.oidc_client_id,
        client_secret=(
            settings.oidc_client_secret.get_secret_value()
            if settings.oidc_client_secret
            else None
        ),
        server_metadata_url=urljoin(
            settings.oidc_issuer_url.rstrip("/") + "/",
            ".well-known/openid-configuration",
        ),
        client_kwargs={
            "scope": settings.oidc_scopes,
            "code_challenge_method": "S256",
        },
    )
    return oauth.create_client("oidc")


def _purge_expired_sessions(now: float) -> None:
    for session_id, (expires_at, _) in list(_sessions.items()):
        if expires_at <= now:
            _sessions.pop(session_id, None)
    for transaction_id, (expires_at, _) in list(_transactions.items()):
        if expires_at <= now:
            _transactions.pop(transaction_id, None)


def get_oidc_user(request: Request) -> AuthenticatedUser | None:
    session_id = request.session.get(_SESSION_KEY)
    if not isinstance(session_id, str):
        return None
    with _sessions_lock:
        _purge_expired_sessions(time.monotonic())
        session = _sessions.get(session_id)
    return session[1] if session else None


async def begin_login(request: Request):
    settings = get_settings()
    if settings.auth_mode != "oidc":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    request.session.clear()
    request.session["oidc_nonce"] = nonce
    request.session["oidc_code_verifier"] = code_verifier
    callback_url = f"{settings.app_base_url.rstrip('/')}/auth/callback"
    try:
        response = await _oauth_client(settings).authorize_redirect(
            request, callback_url, nonce=nonce, code_verifier=code_verifier
        )
    except Exception as exc:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC login is temporarily unavailable.",
        ) from exc
    transaction_id = secrets.token_urlsafe(32)
    with _sessions_lock:
        now = time.monotonic()
        _purge_expired_sessions(now)
        if len(_transactions) >= _MAX_TRANSACTIONS:
            oldest = min(_transactions, key=lambda key: _transactions[key][0])
            _transactions.pop(oldest, None)
        _transactions[transaction_id] = (
            now + _TRANSACTION_TTL_SECONDS,
            dict(request.session),
        )
    request.session.clear()
    request.session[_TRANSACTION_KEY] = transaction_id
    return response


async def complete_login(request: Request) -> AuthenticatedUser:
    settings = get_settings()
    if settings.auth_mode != "oidc":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    transaction_id = request.session.get(_TRANSACTION_KEY)
    transaction = None
    if isinstance(transaction_id, str):
        with _sessions_lock:
            _purge_expired_sessions(time.monotonic())
            stored = _transactions.pop(transaction_id, None)
            transaction = stored[1] if stored else None
    request.session.clear()
    if transaction:
        request.session.update(transaction)
    nonce = request.session.pop("oidc_nonce", None)
    code_verifier = request.session.pop("oidc_code_verifier", None)
    if not isinstance(nonce, str) or not isinstance(code_verifier, str):
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC authentication transaction is missing or expired.",
        )
    try:
        client = _oauth_client(settings)
        # Authlib validates the callback state while exchanging the code.
        token = await client.authorize_access_token(
            request, code_verifier=code_verifier
        )
        # Parse explicitly so nonce validation is mandatory even if Authlib also
        # attached a userinfo mapping to the token response.
        claims = await client.parse_id_token(token, nonce=nonce)
    except Exception as exc:  # Authlib exceptions intentionally remain internal.
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC authentication could not be completed.",
        ) from exc
    if not isinstance(claims, dict) or not claim_value(
        claims, [settings.oidc_user_id_claim]
    ):
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC identity claims are incomplete.",
        )
    user = user_from_claims(claims, settings)
    session_id = secrets.token_urlsafe(32)
    with _sessions_lock:
        _purge_expired_sessions(time.monotonic())
        if len(_sessions) >= _MAX_SESSIONS:
            oldest = min(_sessions, key=lambda key: _sessions[key][0])
            _sessions.pop(oldest, None)
        _sessions[session_id] = (time.monotonic() + _SESSION_TTL_SECONDS, user)
    request.session.clear()
    request.session[_SESSION_KEY] = session_id
    return user


def clear_login(request: Request) -> None:
    session_id = request.session.get(_SESSION_KEY)
    transaction_id = request.session.get(_TRANSACTION_KEY)
    if isinstance(session_id, str) or isinstance(transaction_id, str):
        with _sessions_lock:
            if isinstance(session_id, str):
                _sessions.pop(session_id, None)
            if isinstance(transaction_id, str):
                _transactions.pop(transaction_id, None)
    request.session.clear()


def oidc_diagnostics(request: Request) -> dict[str, object]:
    settings = get_settings()
    user = get_oidc_user(request) if settings.auth_mode == "oidc" else None
    return {
        "auth_mode": settings.auth_mode,
        "configured": settings.auth_mode == "oidc",
        "issuer": settings.oidc_issuer_url if settings.auth_mode == "oidc" else None,
        "client_id_configured": bool(settings.oidc_client_id),
        "client_secret_configured": bool(settings.oidc_client_secret),
        "role_claim_paths": settings.oidc_role_claims,
        "group_claim_paths": settings.oidc_group_claims,
        "authenticated": user is not None,
        "roles": user.roles if user else [],
        "groups": user.groups if user else [],
    }
