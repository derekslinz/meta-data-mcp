"""In-memory OAuth 2.0 Authorization Server provider for meta-data-mcp.

Implements the MCP SDK's ``OAuthAuthorizationServerProvider`` Protocol with
full in-memory storage (tokens are lost on server restart). Suitable for
development, single-node deployments, and operator-managed SSE servers.

Enable OAuth by setting ``META_DATA_MCP_OAUTH_ISSUER`` (e.g.
``http://localhost:8000``). This coexists with the existing bearer-token
auth (``META_DATA_MCP_AUTH_TOKEN``) — both remain valid simultaneously.

Flow (Authorization Code + PKCE + Dynamic Client Registration):
  1. Client POSTs to ``/register`` → receives ``client_id`` + ``client_secret``
  2. Client opens ``/authorize?client_id=…&code_challenge=…&redirect_uri=…``
  3. User is redirected to ``/oauth/consent?session=…``
  4. User approves → ``/oauth/consent/approve`` creates an auth code and
     redirects the browser to the client's ``redirect_uri?code=…&state=…``
  5. Client POSTs to ``/token`` with ``code`` + ``code_verifier`` → tokens
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class InMemoryOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Stateful in-memory OAuth provider.

    All state is local to the process — tokens are lost on restart.
    Thread-safe for asyncio (single-threaded event loop) but not safe for
    multi-process deployments; use a shared cache (Redis/Postgres) for HA.

    Operational note: every server restart (deploy, crash, OOM) invalidates
    all active tokens regardless of the configured token lifetime. Users must
    re-authorize after restarts. For zero-interruption deployments, persist
    token state between restarts or use a rolling-restart strategy.

    Configuration (environment variables):
        META_DATA_MCP_OAUTH_MAX_CLIENTS: Maximum number of registered clients
            (default 1000). Prevents unbounded memory growth in long-running
            deployments. Returns HTTP 400 when the limit is reached.
        META_DATA_MCP_OAUTH_TOKEN_TTL: Access-token lifetime in seconds
            (default 3600 / 1 hour). Keep small to limit exposure if a token
            leaks.
    """

    # Defaults; can be overridden via environment variables.
    _DEFAULT_MAX_CLIENTS: int = 1000
    _DEFAULT_TOKEN_TTL: int = 3600  # 1 hour

    def __init__(self, issuer_url: str) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self._max_clients = int(
            os.getenv("META_DATA_MCP_OAUTH_MAX_CLIENTS", str(self._DEFAULT_MAX_CLIENTS))
        )
        self._token_ttl = int(
            os.getenv("META_DATA_MCP_OAUTH_TOKEN_TTL", str(self._DEFAULT_TOKEN_TTL))
        )
        # Storage maps: key → object
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_sessions: dict[str, dict[str, Any]] = {}  # consent-page sessions
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is required")
        if (
            client_info.client_id not in self._clients
            and len(self._clients) >= self._max_clients
        ):
            raise ValueError(
                f"Maximum number of registered OAuth clients ({self._max_clients}) "
                "reached. Increase META_DATA_MCP_OAUTH_MAX_CLIENTS or remove "
                "unused clients."
            )
        self._clients[client_info.client_id] = client_info

    # ------------------------------------------------------------------
    # Authorization (consent page redirect)
    # ------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Create a short-lived consent session and return the consent page URL."""
        session_token = secrets.token_urlsafe(32)
        self._auth_sessions[session_token] = {
            "client_id": client.client_id,
            "client_name": getattr(client, "client_name", client.client_id),
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "scopes": params.scopes or [],
            "state": params.state,
            "expires_at": time.time() + 43200,  # 12-hour consent window
        }
        return f"{self.issuer_url}/oauth/consent?session={session_token}"

    def peek_session(self, session_token: str) -> dict[str, Any] | None:
        """Return a shallow copy of a pending consent session without consuming it.

        Used by the GET consent page to display session details while leaving
        the session intact for the POST approval step. Returns a shallow copy
        so callers cannot accidentally mutate internal provider state.
        Returns None for unknown or expired sessions.
        """
        session = self._auth_sessions.get(session_token)
        if session is None:
            return None
        if time.time() > session["expires_at"]:
            self._auth_sessions.pop(session_token, None)
            return None
        return dict(session)  # shallow copy — callers cannot mutate stored state

    def consume_session(self, session_token: str) -> dict[str, Any] | None:
        """Retrieve and remove a pending consent session (one-shot)."""
        session = self._auth_sessions.pop(session_token, None)
        if session is None:
            return None
        if time.time() > session["expires_at"]:
            return None
        return session

    def create_authorization_code(self, session: dict[str, Any]) -> str:
        """Issue an authorization code from an approved consent session."""
        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=session["scopes"],
            expires_at=time.time() + 600,  # 10-minute code lifetime
            client_id=session["client_id"],
            code_challenge=session["code_challenge"],
            redirect_uri=session["redirect_uri"],  # type: ignore[arg-type]
            redirect_uri_provided_explicitly=session[
                "redirect_uri_provided_explicitly"
            ],
        )
        return code

    # ------------------------------------------------------------------
    # Authorization code exchange
    # ------------------------------------------------------------------

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code = self._auth_codes.get(authorization_code)
        if code is None:
            return None
        if time.time() > code.expires_at:
            del self._auth_codes[authorization_code]
            return None
        if code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Consume the authorization code and issue access + refresh tokens.

        PKCE validation (code_verifier vs code_challenge) is performed by the
        MCP SDK's ``TokenHandler`` before calling this method — this provider
        does not repeat that check.
        """
        # Remove the used code (one-shot).
        self._auth_codes.pop(authorization_code.code, None)

        access_token_str = secrets.token_urlsafe(32)
        refresh_token_str = secrets.token_urlsafe(32)

        client_id = client.client_id or ""
        self._access_tokens[access_token_str] = AccessToken(
            token=access_token_str,
            client_id=client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + self._token_ttl,
        )
        self._refresh_tokens[refresh_token_str] = RefreshToken(
            token=refresh_token_str,
            client_id=client_id,
            scopes=authorization_code.scopes,
        )

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=self._token_ttl,
            refresh_token=refresh_token_str,
            scope=" ".join(authorization_code.scopes),
        )

    # ------------------------------------------------------------------
    # Refresh token exchange
    # ------------------------------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        rt = self._refresh_tokens.get(refresh_token)
        if rt is None or rt.client_id != client.client_id:
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Issue a new access token (and rotate the refresh token)."""
        # Invalidate the old refresh token.
        self._refresh_tokens.pop(refresh_token.token, None)

        effective_scopes = scopes or refresh_token.scopes
        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        client_id = client.client_id or ""

        self._access_tokens[new_access] = AccessToken(
            token=new_access,
            client_id=client_id,
            scopes=effective_scopes,
            expires_at=int(time.time()) + self._token_ttl,
        )
        self._refresh_tokens[new_refresh] = RefreshToken(
            token=new_refresh,
            client_id=client_id,
            scopes=effective_scopes,
        )

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=self._token_ttl,
            refresh_token=new_refresh,
            scope=" ".join(effective_scopes),
        )

    # ------------------------------------------------------------------
    # Access token verification
    # ------------------------------------------------------------------

    async def verify_access_token(self, token: str) -> AccessToken | None:
        # Use constant-time comparison to avoid timing side-channels.
        # We scan all stored tokens and return the match (or None).
        matched: AccessToken | None = None
        for stored_token, at in self._access_tokens.items():
            if hmac.compare_digest(stored_token, token):
                matched = at
                break
        if matched is None:
            return None
        if matched.expires_at is not None and time.time() > matched.expires_at:
            del self._access_tokens[token]
            return None
        return matched

    # ------------------------------------------------------------------
    # Token revocation
    # ------------------------------------------------------------------

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        self._access_tokens.pop(token.token, None)
        self._refresh_tokens.pop(token.token, None)


# ---------------------------------------------------------------------------
# PKCE helper (used by tests; not part of the Protocol)
# ---------------------------------------------------------------------------


def compute_pkce_challenge(code_verifier: str) -> str:
    """Return the S256 code_challenge for a given verifier."""
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
