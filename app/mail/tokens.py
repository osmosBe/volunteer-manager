"""Microsoft Graph token providers isolated from application mail code."""

from abc import ABC, abstractmethod

import msal

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


class GraphAuthenticationError(RuntimeError):
    """Raised without carrying raw credential or token material."""


class GraphTokenProvider(ABC):
    @abstractmethod
    def get_access_token(self) -> str:
        raise NotImplementedError


class ClientSecretGraphTokenProvider(GraphTokenProvider):
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self._application = msal.ConfidentialClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )

    def get_access_token(self) -> str:
        try:
            result = self._application.acquire_token_silent(GRAPH_SCOPE, account=None)
            if not result:
                result = self._application.acquire_token_for_client(scopes=GRAPH_SCOPE)
        except Exception as exc:
            # MSAL/HTTP exceptions are deliberately normalized so provider and
            # browser diagnostics never receive raw request/credential data.
            raise GraphAuthenticationError(
                "Microsoft Graph authentication failed."
            ) from exc
        token = result.get("access_token") if isinstance(result, dict) else None
        if not token:
            raise GraphAuthenticationError("Microsoft Graph authentication failed.")
        return token


class ManagedIdentityGraphTokenProvider(GraphTokenProvider):
    """Explicit future mode; it never falls back to another credential."""

    def get_access_token(self) -> str:
        raise GraphAuthenticationError(
            "Managed Identity authentication is not implemented in this release."
        )
