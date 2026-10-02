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
import logging
import os
import secrets
import time
from collections.abc import Callable
from itertools import islice
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

log = logging.getLogger(__name__)


def _is_expired(expires_at: float | int | None, now: float) -> bool:
    """True when ``expires_at`` is set and is in the past.

    ``expires_at`` is optional on the SDK's ``AccessToken`` / ``RefreshToken``
    (``int | None``), where ``None`` means "never expires". Such an entry is
    live indefinitely and is bounded by cap eviction instead. Comparing a
    ``None`` against ``now`` would raise ``TypeError``, so it is filtered here
    once rather than at every call site.
    """
    return expires_at is not None and now > expires_at


class InMemoryOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken],
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
        META_DATA_MCP_OAUTH_SWEEP_THRESHOLD: Maximum number of entries in each
            in-memory OAuth store before sweeping and oldest-first eviction
            (default 5000). Must be a positive integer; invalid values fall
            back to the default.
    """

    # Defaults; can be overridden via environment variables.
    _DEFAULT_MAX_CLIENTS: int = 1000
    _DEFAULT_TOKEN_TTL: int = 3600  # 1 hour

    # Hard cap on each in-memory OAuth store (sessions, codes, tokens).
    # Entries past the cap are evicted oldest-first, which bounds memory even
    # when nothing has expired yet. META_DATA_MCP_OAUTH_SWEEP_THRESHOLD
    # tunes it.
    #
    # Overflow handling must stay O(1) per insertion. The flood this defends
    # against is unauthenticated, so a full scan per request would let an
    # attacker buy O(cap) CPU for the price of one request. Eviction is
    # O(excess) and the expiry scan is amortized over _SWEEP_SCAN_INTERVAL
    # insertions instead — see _maybe_sweep().
    _DEFAULT_SWEEP_THRESHOLD: int = 5000

    # How many overflow insertions may pass before a store is scanned for
    # expired entries. At the default cap this bounds the scan's share of a
    # flood's cost to ~1/64th per request, and expiry only ever runs behind
    # genuine overflow (i.e. under flood or at process startup).
    _SWEEP_SCAN_INTERVAL: int = 64

    def __init__(self, issuer_url: str, persistence: Any = None) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self._max_clients = self._read_positive_int_env(
            "META_DATA_MCP_OAUTH_MAX_CLIENTS",
            self._DEFAULT_MAX_CLIENTS,
        )
        self._token_ttl = self._read_positive_int_env(
            "META_DATA_MCP_OAUTH_TOKEN_TTL",
            self._DEFAULT_TOKEN_TTL,
        )
        self._sweep_threshold = self._read_positive_int_env(
            "META_DATA_MCP_OAUTH_SWEEP_THRESHOLD",
            self._DEFAULT_SWEEP_THRESHOLD,
        )
        # Storage maps: key → object
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_sessions: dict[str, dict[str, Any]] = {}  # consent-page sessions
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        # Email identity side-maps for the magic-link gate. The MCP SDK's
        # AuthorizationCode/AccessToken types have no field for a verified
        # email, so we carry it alongside: session → code → token. Empty
        # entries simply mean "no email gate" (e.g. static-token auth).
        self._code_email: dict[str, str] = {}
        self._token_email: dict[str, str] = {}
        # Refresh tokens carry the email too, so a refresh exchange re-binds the
        # new access token. Without this, refreshing would drop the identity and
        # let a user escape per-email rate limiting by rotating tokens.
        self._refresh_email: dict[str, str] = {}
        self._cap_warning_emitted = False
        # Overflow insertions since each store was last scanned for expired
        # entries, keyed by id(store). Drives the amortized scan in
        # _maybe_sweep(); entries are removed when the store is back under cap.
        self._sweep_scan_counters: dict[int, int] = {}
        # Optional durable backend (SqliteOAuthPersistence). The dicts above stay
        # the working set; when persistence is present we load it on startup and
        # write-through every durable mutation. None → pure in-memory (default).
        self._persistence = persistence
        if persistence is not None:
            self._clients.update(persistence.load_clients())
            access_tokens, token_email = persistence.load_access_tokens(
                limit=self._sweep_threshold,
            )
            self._access_tokens.update(access_tokens)
            self._token_email.update(token_email)
            refresh_tokens, refresh_email = persistence.load_refresh_tokens(
                limit=self._sweep_threshold,
            )
            self._refresh_tokens.update(refresh_tokens)
            self._refresh_email.update(refresh_email)
            self._maybe_sweep()
            log.info(
                "Loaded OAuth state from persistence: %d clients, %d access "
                "tokens, %d refresh tokens",
                len(self._clients),
                len(self._access_tokens),
                len(self._refresh_tokens),
            )

    @staticmethod
    def _read_positive_int_env(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            log.warning("%s must be an integer; using default %d", name, default)
            return default
        if value <= 0:
            log.warning("%s must be > 0; using default %d", name, default)
            return default
        return value

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
                "unused clients.",
            )
        self._clients[client_info.client_id] = client_info
        if self._persistence is not None:
            self._persistence.save_client(client_info)

    # ------------------------------------------------------------------
    # Authorization (consent page redirect)
    # ------------------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
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
        self._maybe_sweep(self._auth_sessions)
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

    # Expiry is read through these per-store getters rather than off the SDK
    # types directly, because ``expires_at`` is optional (``int | None``) on
    # AccessToken and RefreshToken — a ``None`` means "never expires" and must
    # survive the sweep, exactly as ``verify_access_token()`` already treats it.
    @staticmethod
    def _session_expiry(session: dict[str, Any]) -> float | None:
        return session["expires_at"]

    @staticmethod
    def _code_expiry(code: AuthorizationCode) -> float | None:
        return code.expires_at

    @staticmethod
    def _access_expiry(token: AccessToken) -> float | None:
        return token.expires_at

    def sweep_expired(self) -> int:
        """Drop expired sessions, codes and tokens. Returns the count removed.

        Every store here is otherwise only pruned when a caller presents the
        matching secret, so a secret that is never presented is never freed.
        Consent sessions are the cheapest to abuse: creating one needs only
        an unauthenticated ``GET /authorize`` and no consent at all.
        """
        return sum(self._sweep_store(*spec) for spec in self._reapers())

    def _reapers(self) -> tuple[tuple[dict, Callable, Callable], ...]:
        """(store, expiry-getter, on-drop) triples for every bounded store.

        ``RefreshToken.expires_at`` exists on the SDK type and is optional, but
        this provider never sets it — a refresh token is invalidated by
        rotation or revocation, not by age. The getter therefore yields
        ``None`` ("never expires") and those tokens are bounded by cap
        eviction alone. They are still swept so that if a caller ever starts
        setting the field, expiry is honoured for free.
        """
        return (
            (self._auth_sessions, self._session_expiry, self._drop_sessions),
            (self._auth_codes, self._code_expiry, self._drop_codes),
            (self._access_tokens, self._access_expiry, self._drop_access_tokens),
            (
                self._refresh_tokens,
                self._access_expiry,  # same optional field, same None semantics
                self._drop_refresh_tokens,
            ),
        )

    def _sweep_store(
        self,
        store: dict,
        expiry_of: Callable[[Any], float | None],
        on_drop: Callable[[list[str]], None],
    ) -> int:
        """Reap one store's expired entries. Returns how many were removed."""
        now = time.time()
        expired = [k for k, v in store.items() if _is_expired(expiry_of(v), now)]
        if not expired:
            return 0
        for key in expired:
            del store[key]
        on_drop(expired)
        return len(expired)

    @staticmethod
    def _drop_sessions(keys: list[str]) -> None:
        """Consent sessions carry no side-map entries and are never persisted."""
        del keys

    def _drop_codes(self, keys: list[str]) -> None:
        for key in keys:
            self._code_email.pop(key, None)

    def _drop_access_tokens(self, keys: list[str]) -> None:
        for key in keys:
            self._token_email.pop(key, None)
        if self._persistence is not None:
            # One transaction for the whole batch: a sweep can reap thousands
            # of tokens, and a DELETE per row would stall the event loop.
            self._persistence.delete_access_tokens(keys)

    def _drop_refresh_tokens(self, keys: list[str]) -> None:
        for key in keys:
            self._refresh_email.pop(key, None)
        if self._persistence is not None:
            self._persistence.delete_refresh_tokens(keys)

    def _evict_over_cap(self, store: dict, cap: int) -> list[str]:
        """Drop oldest entries from ``store`` until it is under ``cap``.

        Expiry alone cannot bound these stores: a consent session lives 12
        hours, so a caller can mint unlimited sessions inside one window and
        none of them are expired yet, so the sweep reaps nothing and the
        dict grows without limit. Eviction is what actually caps memory, so it
        is applied independently of age.

        Dict insertion order is the creation order, and refresh rotation pops
        the old token before inserting its replacement, so first-inserted is
        genuinely the oldest entry. Whether that entry is still live is not
        checked here — eviction is age-blind, and the periodic sweep is what
        reclaims entries that have genuinely expired.
        """
        excess = len(store) - cap
        if excess <= 0:
            return []
        # islice, not list(store)[:excess]: the slice copies the whole store
        # just to name a handful of keys, and this runs on the hot path once
        # a store is at its cap.
        evicted = list(islice(store, excess))
        for key in evicted:
            del store[key]
        return evicted

    def _maybe_sweep(self, store: dict | None = None) -> None:
        """Enforce the hard cap on the named store, reaping expired entries.

        Called after insertions so each store is bounded regardless of which
        OAuth flow created the entry. When ``store`` is given, only that store
        is examined — the caller knows which one just grew, and scanning the
        others would make every request pay for the whole provider's state.
        With ``store=None`` every store is checked, which is what startup and
        tests want.

        Cost is O(1) amortized per insertion, not O(cap):

        * Eviction runs every time, but only touches ``excess`` entries.
        * The expiry scan — the only O(cap) part — runs at most once per
          ``_SWEEP_SCAN_INTERVAL`` overflow insertions.

        A flood of unauthenticated ``GET /authorize`` calls keeps a store
        pinned at the cap, where every entry is still inside the 12-hour
        consent window. Scanning on each request made each attacker-controlled
        call pay O(cap) (~0.3ms at the default cap) to evict a single entry,
        which is the CPU-amplification shape the cap exists to prevent.
        """
        specs = self._reapers()
        if store is not None:
            specs = tuple(spec for spec in specs if spec[0] is store)
            if not specs:
                return
        counters = self._sweep_scan_counters
        for target, expiry_of, on_drop in specs:
            key = id(target)
            if len(target) <= self._sweep_threshold:
                # Back under the cap: no work owed, and reset the scan budget
                # so the next overflow pays for a fresh scan.
                counters.pop(key, None)
                continue
            if counters.get(key, 0) + 1 >= self._SWEEP_SCAN_INTERVAL:
                counters[key] = 0
                self._sweep_store(target, expiry_of, on_drop)
            else:
                counters[key] = counters.get(key, 0) + 1
            evicted = self._evict_over_cap(target, self._sweep_threshold)
            if not evicted:
                continue
            on_drop(evicted)
            if not self._cap_warning_emitted:
                self._cap_warning_emitted = True
                log.warning(
                    "OAuth in-memory cap reached: dropped %d oldest %s from a "
                    "store at the threshold of %d. Eviction is age-blind, so "
                    "these may be expired entries or entries still in use; it "
                    "does not by itself mean live sessions were displaced. "
                    "Expected under a client flood; if it recurs while load is "
                    "normal, raise META_DATA_MCP_OAUTH_SWEEP_THRESHOLD. Further "
                    "warnings are suppressed until restart.",
                    len(evicted),
                    "entry" if len(evicted) == 1 else "entries",
                    self._sweep_threshold,
                )

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
        # Carry a verified email (set by the magic-link gate) onto the code so
        # it survives the exchange and lands on the access token.
        email = session.get("email")
        if email:
            self._code_email[code] = email
        self._maybe_sweep(self._auth_codes)
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
        # Move any verified email from the code to the issued access token so
        # the rate limiter and identity lookups can key on it.
        email = self._code_email.pop(authorization_code.code, None)

        access_token_str = secrets.token_urlsafe(32)
        refresh_token_str = secrets.token_urlsafe(32)

        client_id = client.client_id or ""
        access_token = AccessToken(
            token=access_token_str,
            client_id=client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + self._token_ttl,
        )
        self._access_tokens[access_token_str] = access_token
        if email:
            self._token_email[access_token_str] = email
            self._refresh_email[refresh_token_str] = email
        refresh_token = RefreshToken(
            token=refresh_token_str,
            client_id=client_id,
            scopes=authorization_code.scopes,
        )
        self._refresh_tokens[refresh_token_str] = refresh_token

        if self._persistence is not None:
            self._persistence.save_access_token(access_token_str, access_token, email)
            self._persistence.save_refresh_token(
                refresh_token_str,
                refresh_token,
                email,
            )
            # A verified-email token issuance is a sign-in — record it for audit.
            if email:
                self._persistence.record_signin(email, client_id, time.time())

        # This grant inserts into both token stores, so both must be capped.
        # Sweeping only the access-token store let repeated authorization-code
        # grants grow _refresh_tokens (and the persisted refresh-token table)
        # without bound.
        self._maybe_sweep(self._access_tokens)
        self._maybe_sweep(self._refresh_tokens)
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
        # Invalidate the old refresh token, carrying its bound email forward so
        # the rotated access token keeps the same rate-limit identity.
        self._refresh_tokens.pop(refresh_token.token, None)
        email = self._refresh_email.pop(refresh_token.token, None)

        effective_scopes = scopes or refresh_token.scopes
        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        client_id = client.client_id or ""

        new_access_token = AccessToken(
            token=new_access,
            client_id=client_id,
            scopes=effective_scopes,
            expires_at=int(time.time()) + self._token_ttl,
        )
        self._access_tokens[new_access] = new_access_token
        new_refresh_token = RefreshToken(
            token=new_refresh,
            client_id=client_id,
            scopes=effective_scopes,
        )
        self._refresh_tokens[new_refresh] = new_refresh_token
        if email:
            self._token_email[new_access] = email
            self._refresh_email[new_refresh] = email

        if self._persistence is not None:
            self._persistence.delete_refresh_token(refresh_token.token)
            self._persistence.save_access_token(new_access, new_access_token, email)
            self._persistence.save_refresh_token(new_refresh, new_refresh_token, email)

        # Rotation touches both token stores; check them together so neither
        # can drift over the cap unnoticed.
        self._maybe_sweep(self._access_tokens)
        self._maybe_sweep(self._refresh_tokens)
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
        matched: tuple[str, AccessToken] | None = None
        for stored_token, at in self._access_tokens.items():
            if hmac.compare_digest(stored_token, token):
                matched = (stored_token, at)
        if matched is None:
            return None
        matched_token, matched_access_token = matched
        if (
            matched_access_token.expires_at is not None
            and time.time() > matched_access_token.expires_at
        ):
            del self._access_tokens[matched_token]
            self._token_email.pop(matched_token, None)
            if self._persistence is not None:
                self._persistence.delete_access_token(matched_token)
            return None
        return matched_access_token

    def email_for_token(self, token: str) -> str | None:
        """Return the verified email bound to ``token`` by the magic-link gate.

        ``None`` means the token was issued without an email gate (e.g. static
        bearer-token auth) — callers fall back to the token itself as identity.
        """
        return self._token_email.get(token)

    # ------------------------------------------------------------------
    # Token revocation
    # ------------------------------------------------------------------

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        self._access_tokens.pop(token.token, None)
        self._refresh_tokens.pop(token.token, None)
        self._token_email.pop(token.token, None)
        self._refresh_email.pop(token.token, None)
        if self._persistence is not None:
            # A revoked token could be either kind; clear both stores.
            self._persistence.delete_access_token(token.token)
            self._persistence.delete_refresh_token(token.token)


# ---------------------------------------------------------------------------
# PKCE helper (used by tests; not part of the Protocol)
# ---------------------------------------------------------------------------


def compute_pkce_challenge(code_verifier: str) -> str:
    """Return the S256 code_challenge for a given verifier."""
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
