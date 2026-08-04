"""OAuth consent + magic-link HTTP handlers.

Extracted from the ``run_server`` closure so the consent / approval / magic-link
flow can be unit-tested directly (build a :class:`ConsentRoutes`, mount
``.routes()`` on a throwaway Starlette app, drive it over ASGITransport) instead
of only indirectly through the provider and middleware.

The class captures the same dependencies the closure did — the OAuth provider,
the issuer URL, and (when the email gate is on) the magic-link store and
emailer — and exposes each handler as a method plus a :meth:`routes` builder.
"""

from __future__ import annotations

import html as _html
import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from meta_data_mcp.emailer import magic_link_message

log = logging.getLogger(__name__)

_CONSENT_STYLE = """<style>body{font-family:sans-serif;max-width:480px;margin:3rem auto;padding:0 1rem}
  .card{border:1px solid #ddd;border-radius:8px;padding:1.5rem}
  h2{margin-top:0} .scope{color:#555;font-size:.9rem}
  input[type=email]{width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;font-size:1rem;margin:.5rem 0 1rem;box-sizing:border-box}
  button{padding:.6rem 1.4rem;border:none;border-radius:4px;cursor:pointer;font-size:1rem}
  .approve{background:#2563eb;color:#fff} .deny{background:#e5e7eb;color:#111;margin-left:.5rem}
</style>"""


class ConsentRoutes:
    """OAuth consent-page and magic-link route handlers."""

    def __init__(
        self,
        *,
        oauth_provider: Any,
        issuer_url: str,
        email_gate_enabled: bool,
        magic_store: Any = None,
        emailer: Any = None,
        ip_rate_limiter: Any = None,
        email_rate_limiter: Any = None,
    ) -> None:
        self.oauth_provider = oauth_provider
        self.issuer_url = issuer_url.rstrip("/")
        self.email_gate_enabled = email_gate_enabled
        self.magic_store = magic_store
        self.emailer = emailer
        # Anti-abuse throttles for the request-link endpoint. Both are optional
        # RateLimiter-shaped objects (``.allow(identity)`` / ``.retry_after``).
        self.ip_rate_limiter = ip_rate_limiter
        self.email_rate_limiter = email_rate_limiter

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_email(value: str) -> bool:
        """Cheap structural email check — not RFC-5322, just enough to reject
        obvious junk before we send. Final proof of validity is whether the
        magic link is actually received and clicked.
        """
        value = value.strip()
        if not (3 <= len(value) <= 254) or " " in value:
            return False
        local, _, domain = value.partition("@")
        return bool(local) and "." in domain and not domain.startswith(".")

    @staticmethod
    def _add_query_params(base_url: str, params: dict[str, str]) -> str:
        """Append params to base_url, preserving any existing query string.

        Uses list-of-tuples rather than a dict so that existing duplicate or
        blank-value query params are never silently dropped. Only the keys
        explicitly provided in ``params`` are added/overwritten.
        """
        parts = urlsplit(base_url)
        existing = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k not in params
        ]
        merged = existing + list(params.items())
        return urlunsplit(parts._replace(query=urlencode(merged)))

    @staticmethod
    def _client_ip(request: Request) -> str:
        """Best-effort client IP for rate limiting.

        The production deployment sits behind a trusted TLS reverse proxy (see
        docs/hosting.md), so honor the left-most ``X-Forwarded-For`` hop when
        present; otherwise fall back to the direct peer. XFF is spoofable if the
        server is exposed without a proxy — the throttle is a courtesy control,
        not an authz boundary, so that's acceptable.
        """
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _check_email_page(self, email: str) -> HTMLResponse:
        """The 'we sent you a link' page — shown on real sends AND on silent
        per-email throttling, so an attacker can't tell a bombed address from a
        delivered one.
        """
        email_escaped = _html.escape(email)
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Check your email — meta-data-mcp</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:3rem auto;padding:0 1rem}}
  .card{{border:1px solid #ddd;border-radius:8px;padding:1.5rem}}</style>
</head><body><div class="card">
  <h2>Check your email</h2>
  <p>We sent a single-use sign-in link to <strong>{email_escaped}</strong>.
  Open it on this device to finish connecting. The link expires shortly.</p>
</div></body></html>""")

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def consent_get(self, request: Request) -> HTMLResponse:
        """GET /oauth/consent?session=<token> — render the consent page."""
        session_token = request.query_params.get("session", "")
        # Peek (don't consume) so the GET page can render while the POST step
        # still finds the session.
        session = self.oauth_provider.peek_session(session_token)
        if session is None:
            return HTMLResponse("<h1>Session expired or invalid.</h1>", status_code=400)
        # HTML-escape every value derived from dynamic client registration to
        # prevent XSS via a crafted client_name or scope.
        client_name = _html.escape(
            str(session.get("client_name", session.get("client_id", "?"))),
        )
        scopes_html = _html.escape(", ".join(session.get("scopes", [])) or "(default)")
        session_token_escaped = _html.escape(session_token)
        if self.email_gate_enabled:
            body_html = f"""<div class="card">
  <h2>Sign in to meta-data-mcp</h2>
  <p><strong>{client_name}</strong> is requesting access. Enter your email and
  we'll send you a single-use sign-in link.</p>
  <p class="scope">Requested scopes: {scopes_html}</p>
  <form method="POST" action="/oauth/consent/request-link">
    <input type="hidden" name="session" value="{session_token_escaped}">
    <input type="email" name="email" placeholder="you@example.com" required autofocus>
    <button type="submit" class="approve">Email me a sign-in link</button>
    <button type="submit" name="deny" value="1" class="deny" formaction="/oauth/consent/approve" formnovalidate>Deny</button>
  </form>
</div>"""
        else:
            body_html = f"""<div class="card">
  <h2>Authorize access</h2>
  <p><strong>{client_name}</strong> is requesting access to your meta-data-mcp server.</p>
  <p class="scope">Requested scopes: {scopes_html}</p>
  <form method="POST" action="/oauth/consent/approve">
    <input type="hidden" name="session" value="{session_token_escaped}">
    <button type="submit" class="approve">Approve</button>
    <button type="submit" name="deny" value="1" class="deny">Deny</button>
  </form>
</div>"""
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Authorize — meta-data-mcp</title>
{_CONSENT_STYLE}</head><body>
{body_html}</body></html>""")

    async def consent_post(self, request: Request):
        """POST /oauth/consent/approve — approve or deny (non-gated path)."""
        form = await request.form()
        session_token = str(form.get("session", ""))
        session = self.oauth_provider.consume_session(session_token)
        if session is None:
            return HTMLResponse("<h1>Session expired or invalid.</h1>", status_code=400)
        if form.get("deny"):
            params: dict[str, str] = {"error": "access_denied"}
            if session.get("state"):
                params["state"] = session["state"]
            return RedirectResponse(
                self._add_query_params(session["redirect_uri"], params),
                status_code=302,
            )
        code = self.oauth_provider.create_authorization_code(session)
        params = {"code": code}
        if session.get("state"):
            params["state"] = session["state"]
        return RedirectResponse(
            self._add_query_params(session["redirect_uri"], params),
            status_code=302,
        )

    async def request_link_post(self, request: Request) -> HTMLResponse:
        """POST /oauth/consent/request-link — email a single-use sign-in link."""
        form = await request.form()
        session_token = str(form.get("session", ""))
        email = str(form.get("email", "")).strip()
        # Peek (don't consume): the session must survive until the user clicks
        # the magic link, where consume_session finalizes it.
        session = self.oauth_provider.peek_session(session_token)
        if session is None:
            return HTMLResponse("<h1>Session expired or invalid.</h1>", status_code=400)
        if not self._looks_like_email(email):
            return HTMLResponse(
                "<h1>Please enter a valid email address.</h1>"
                "<p><a href='javascript:history.back()'>Go back</a></p>",
                status_code=400,
            )
        # Anti-abuse: cap requests per client IP (abusive sprayer → 429) and
        # per target email (email-bombing a victim → silently drop but show the
        # normal page, so the attacker can't distinguish a bombed address).
        if self.ip_rate_limiter is not None:
            ip = self._client_ip(request)
            if not self.ip_rate_limiter.allow(f"ip:{ip}"):
                retry_after = self.ip_rate_limiter.retry_after(f"ip:{ip}")
                return HTMLResponse(
                    "<h1>Too many sign-in requests.</h1>"
                    "<p>Please wait a bit and try again.</p>",
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        if self.email_rate_limiter is not None:
            if not self.email_rate_limiter.allow(f"email:{email.lower()}"):
                log.warning("Magic-link request throttled for email %s", email)
                return self._check_email_page(email)

        assert self.magic_store is not None and self.emailer is not None
        magic_token = self.magic_store.issue(session_token, email)
        link = f"{self.issuer_url}/oauth/magic?token={magic_token}"
        try:
            await self.emailer.send(magic_link_message(email, link))
        except Exception:
            log.exception("Failed to send magic-link email to %s", email)
            return HTMLResponse(
                "<h1>Couldn't send the sign-in email.</h1>"
                "<p>Please try again in a moment.</p>",
                status_code=502,
            )
        return self._check_email_page(email)

    async def magic_get(self, request: Request):
        """GET /oauth/magic?token=<token> — verify the link and complete OAuth."""
        token = request.query_params.get("token", "")
        assert self.magic_store is not None
        record = self.magic_store.verify(token)
        if record is None:
            return HTMLResponse(
                "<h1>This sign-in link is invalid or has expired.</h1>"
                "<p>Start the connection again to get a new link.</p>",
                status_code=400,
            )
        session = self.oauth_provider.consume_session(record.session_token)
        if session is None:
            return HTMLResponse(
                "<h1>Your sign-in session expired.</h1>"
                "<p>Start the connection again.</p>",
                status_code=400,
            )
        # Bind the verified email so it flows session → code → token.
        session["email"] = record.email
        code = self.oauth_provider.create_authorization_code(session)
        params = {"code": code}
        if session.get("state"):
            params["state"] = session["state"]
        return RedirectResponse(
            self._add_query_params(session["redirect_uri"], params),
            status_code=302,
        )

    # ------------------------------------------------------------------
    # Route table
    # ------------------------------------------------------------------

    def routes(self) -> list[Route]:
        """Return the consent/magic routes, including the gated ones when the
        email gate is enabled.
        """
        routes = [
            Route("/oauth/consent", endpoint=self.consent_get, methods=["GET"]),
            Route(
                "/oauth/consent/approve",
                endpoint=self.consent_post,
                methods=["POST"],
            ),
        ]
        if self.email_gate_enabled:
            routes += [
                Route(
                    "/oauth/consent/request-link",
                    endpoint=self.request_link_post,
                    methods=["POST"],
                ),
                Route("/oauth/magic", endpoint=self.magic_get, methods=["GET"]),
            ]
        return routes
