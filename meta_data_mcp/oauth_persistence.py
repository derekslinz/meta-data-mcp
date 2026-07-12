"""SQLite persistence for the OAuth provider (durable tokens + sign-in audit).

By default :class:`~meta_data_mcp.oauth_provider.InMemoryOAuthProvider` keeps
everything in process memory, so a restart drops every registered client and
issued token — connected MCP clients must re-authorize. Wiring a
:class:`SqliteOAuthPersistence` into the provider makes the *durable* state
survive a restart:

- **clients** — dynamically-registered OAuth clients.
- **access tokens** / **refresh tokens** — including their bound email (the
  magic-link identity) so per-user rate limiting is preserved across restarts.
- **sign-in audit** — one row per verified-email token issuance, queryable for
  "who connected, when".

Transient handshake state (consent sessions, authorization codes) is *not*
persisted: it lives for minutes and a restart mid-handshake just means the
client retries. The provider keeps its in-memory dicts as the working set and
write-throughs each durable mutation here, loading everything back on startup.

Storage is a single stdlib ``sqlite3`` connection guarded by a lock (the server
runs single-worker; see docs/hosting.md). Volume is auth-event-rate, not
request-rate, so blocking writes on the event loop are acceptable.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from mcp.server.auth.provider import AccessToken, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    data      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS access_tokens (
    token      TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    expires_at INTEGER,
    email      TEXT
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token TEXT PRIMARY KEY,
    data  TEXT NOT NULL,
    email TEXT
);
CREATE TABLE IF NOT EXISTS signins (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    email     TEXT NOT NULL,
    client_id TEXT,
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signins_ts ON signins (ts);
"""


@dataclass(frozen=True)
class SigninEvent:
    """One verified-email sign-in (token issuance)."""

    email: str
    client_id: str | None
    ts: float


class SqliteOAuthPersistence:
    """Durable OAuth state + sign-in audit backed by SQLite."""

    def __init__(self, db_path: str) -> None:
        self._lock = threading.Lock()
        # Fail with a clear message (and create the parent dir) rather than
        # sqlite's opaque "unable to open database file" when e.g.
        # /var/lib/meta-data-mcp/ doesn't exist yet.
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: anyio may run handlers on worker threads; the
        # lock serializes access so the single connection is used safely.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
        # Expired access tokens are otherwise deleted only if that exact token
        # is re-presented; refresh rotation strands one dead row per cycle.
        # Purge on startup so the DB (and the in-memory working set loaded from
        # it) doesn't grow without bound.
        purged = self.purge_expired_access_tokens()
        if purged:
            log.info("Purged %d expired access token(s) from %s", purged, db_path)

    def purge_expired_access_tokens(self, now: float | None = None) -> int:
        """Delete access tokens whose ``expires_at`` has passed; return count."""
        cutoff = time.time() if now is None else now
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM access_tokens "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (cutoff,),
            )
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    def save_client(self, client: OAuthClientInformationFull) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO clients (client_id, data) VALUES (?, ?)",
                (client.client_id, client.model_dump_json()),
            )

    def load_clients(self) -> dict[str, OAuthClientInformationFull]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM clients").fetchall()
        out: dict[str, OAuthClientInformationFull] = {}
        for row in rows:
            client = OAuthClientInformationFull.model_validate_json(row["data"])
            if client.client_id is not None:
                out[client.client_id] = client
        return out

    # ------------------------------------------------------------------
    # Access tokens (with bound email)
    # ------------------------------------------------------------------

    def save_access_token(
        self, token: str, access_token: AccessToken, email: str | None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO access_tokens "
                "(token, data, expires_at, email) VALUES (?, ?, ?, ?)",
                (
                    token,
                    access_token.model_dump_json(),
                    access_token.expires_at,
                    email,
                ),
            )

    def delete_access_token(self, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM access_tokens WHERE token = ?", (token,))

    def load_access_tokens(self) -> tuple[dict[str, AccessToken], dict[str, str]]:
        """Return (token → AccessToken, token → email) maps."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT token, data, email FROM access_tokens"
            ).fetchall()
        tokens: dict[str, AccessToken] = {}
        emails: dict[str, str] = {}
        for row in rows:
            tokens[row["token"]] = AccessToken.model_validate_json(row["data"])
            if row["email"]:
                emails[row["token"]] = row["email"]
        return tokens, emails

    # ------------------------------------------------------------------
    # Refresh tokens (with bound email)
    # ------------------------------------------------------------------

    def save_refresh_token(
        self, token: str, refresh_token: RefreshToken, email: str | None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO refresh_tokens (token, data, email) "
                "VALUES (?, ?, ?)",
                (token, refresh_token.model_dump_json(), email),
            )

    def delete_refresh_token(self, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))

    def load_refresh_tokens(self) -> tuple[dict[str, RefreshToken], dict[str, str]]:
        """Return (token → RefreshToken, token → email) maps."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT token, data, email FROM refresh_tokens"
            ).fetchall()
        tokens: dict[str, RefreshToken] = {}
        emails: dict[str, str] = {}
        for row in rows:
            tokens[row["token"]] = RefreshToken.model_validate_json(row["data"])
            if row["email"]:
                emails[row["token"]] = row["email"]
        return tokens, emails

    # ------------------------------------------------------------------
    # Sign-in audit log
    # ------------------------------------------------------------------

    def record_signin(self, email: str, client_id: str | None, ts: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO signins (email, client_id, ts) VALUES (?, ?, ?)",
                (email, client_id, ts),
            )

    def recent_signins(self, limit: int = 50) -> list[SigninEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT email, client_id, ts FROM signins ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            SigninEvent(email=r["email"], client_id=r["client_id"], ts=r["ts"])
            for r in rows
        ]
