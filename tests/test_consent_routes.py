"""End-to-end HTTP tests for the consent / magic-link handlers.

These exercise the real Starlette routes (via ASGITransport) that were
previously buried in the ``run_server`` closure and only testable indirectly.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.applications import Starlette

from meta_data_mcp.auth_gate import MagicLinkStore, RateLimiter
from meta_data_mcp.consent_routes import ConsentRoutes
from meta_data_mcp.emailer import EmailMessage
from meta_data_mcp.oauth_provider import InMemoryOAuthProvider


@pytest.fixture
def anyio_backend():
    return "asyncio"


class RecordingEmailer:
    """Captures sent messages; optionally raises to simulate a send failure."""

    def __init__(self, fail: bool = False):
        self.sent: list[EmailMessage] = []
        self.fail = fail

    async def send(self, message: EmailMessage) -> None:
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append(message)


@pytest.fixture
def provider():
    return InMemoryOAuthProvider(issuer_url="http://localhost:8000")


async def _new_session(provider) -> str:
    """Create a pending consent session and return its token."""
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    from meta_data_mcp.oauth_provider import compute_pkce_challenge

    client = OAuthClientInformationFull(client_id="c1", redirect_uris=None)
    params = AuthorizationParams(
        state="xyz",
        scopes=["opendata"],
        code_challenge=compute_pkce_challenge("verifier123"),
        redirect_uri="http://localhost/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=False,
    )
    url = await provider.authorize(client, params)
    return url.split("session=")[1]


def _app(
    provider,
    *,
    email_gate=False,
    emailer=None,
    magic_store=None,
    ip_limiter=None,
    email_limiter=None,
):
    routes = ConsentRoutes(
        oauth_provider=provider,
        issuer_url="http://localhost:8000",
        email_gate_enabled=email_gate,
        magic_store=magic_store,
        emailer=emailer,
        ip_rate_limiter=ip_limiter,
        email_rate_limiter=email_limiter,
    ).routes()
    return Starlette(routes=routes)


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


# ---------------------------------------------------------------------------
# consent_get
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consent_get_gated_shows_email_form(provider):
    session = await _new_session(provider)
    app = _app(
        provider,
        email_gate=True,
        emailer=RecordingEmailer(),
        magic_store=MagicLinkStore(),
    )
    async with _client(app) as c:
        r = await c.get(f"/oauth/consent?session={session}")
    assert r.status_code == 200
    assert 'type="email"' in r.text
    assert "/oauth/consent/request-link" in r.text


@pytest.mark.anyio
async def test_consent_get_ungated_shows_approve(provider):
    session = await _new_session(provider)
    app = _app(provider, email_gate=False)
    async with _client(app) as c:
        r = await c.get(f"/oauth/consent?session={session}")
    assert r.status_code == 200
    assert "Approve" in r.text
    assert 'type="email"' not in r.text


@pytest.mark.anyio
async def test_consent_get_invalid_session(provider):
    app = _app(provider)
    async with _client(app) as c:
        r = await c.get("/oauth/consent?session=nope")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# consent_post (approve / deny)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consent_post_approve_redirects_with_code(provider):
    session = await _new_session(provider)
    app = _app(provider)
    async with _client(app) as c:
        r = await c.post(
            "/oauth/consent/approve", data={"session": session}, follow_redirects=False
        )
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert "code" in q
    assert q["state"] == ["xyz"]


@pytest.mark.anyio
async def test_consent_post_deny_redirects_with_error(provider):
    session = await _new_session(provider)
    app = _app(provider)
    async with _client(app) as c:
        r = await c.post(
            "/oauth/consent/approve",
            data={"session": session, "deny": "1"},
            follow_redirects=False,
        )
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["error"] == ["access_denied"]


# ---------------------------------------------------------------------------
# request_link_post
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_request_link_sends_email_and_issues_token(provider):
    session = await _new_session(provider)
    emailer = RecordingEmailer()
    store = MagicLinkStore()
    app = _app(provider, email_gate=True, emailer=emailer, magic_store=store)
    async with _client(app) as c:
        r = await c.post(
            "/oauth/consent/request-link",
            data={"session": session, "email": "user@example.com"},
        )
    assert r.status_code == 200
    assert "Check your email" in r.text
    assert len(emailer.sent) == 1
    # The emailed link carries a token that verifies to this session + email.
    link = emailer.sent[0].text
    token = link.split("token=")[1].split()[0].strip()
    record = store.verify(token)
    assert record is not None
    assert record.session_token == session
    assert record.email == "user@example.com"


@pytest.mark.anyio
async def test_request_link_rejects_bad_email(provider):
    session = await _new_session(provider)
    emailer = RecordingEmailer()
    app = _app(provider, email_gate=True, emailer=emailer, magic_store=MagicLinkStore())
    async with _client(app) as c:
        r = await c.post(
            "/oauth/consent/request-link",
            data={"session": session, "email": "not-an-email"},
        )
    assert r.status_code == 400
    assert emailer.sent == []


@pytest.mark.anyio
async def test_request_link_invalid_session(provider):
    app = _app(
        provider,
        email_gate=True,
        emailer=RecordingEmailer(),
        magic_store=MagicLinkStore(),
    )
    async with _client(app) as c:
        r = await c.post(
            "/oauth/consent/request-link",
            data={"session": "nope", "email": "user@example.com"},
        )
    assert r.status_code == 400


@pytest.mark.anyio
async def test_request_link_email_send_failure_returns_502(provider):
    session = await _new_session(provider)
    app = _app(
        provider,
        email_gate=True,
        emailer=RecordingEmailer(fail=True),
        magic_store=MagicLinkStore(),
    )
    async with _client(app) as c:
        r = await c.post(
            "/oauth/consent/request-link",
            data={"session": session, "email": "user@example.com"},
        )
    assert r.status_code == 502


# ---------------------------------------------------------------------------
# request_link_post — anti-abuse throttles
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_request_link_ip_throttle_returns_429(provider):
    """A client IP over the request cap gets 429 (abusive sprayer)."""
    emailer = RecordingEmailer()
    app = _app(
        provider,
        email_gate=True,
        emailer=emailer,
        magic_store=MagicLinkStore(),
        ip_limiter=RateLimiter(rpm=2, window_seconds=900),
    )
    headers = {"x-forwarded-for": "9.9.9.9"}
    async with _client(app) as c:
        statuses = []
        for _ in range(3):
            session = await _new_session(provider)
            r = await c.post(
                "/oauth/consent/request-link",
                data={"session": session, "email": "user@example.com"},
                headers=headers,
            )
            statuses.append(r.status_code)
    assert statuses == [200, 200, 429]
    assert len(emailer.sent) == 2  # the 429'd request sent nothing


@pytest.mark.anyio
async def test_request_link_email_throttle_silently_drops(provider):
    """Over-limit for one email returns the normal page but sends no email, so
    a victim can't be bombed and an attacker can't detect the throttle."""
    emailer = RecordingEmailer()
    app = _app(
        provider,
        email_gate=True,
        emailer=emailer,
        magic_store=MagicLinkStore(),
        email_limiter=RateLimiter(rpm=1, window_seconds=900),
    )
    async with _client(app) as c:
        results = []
        for i in range(3):
            session = await _new_session(provider)
            # Vary the source IP so only the per-email limiter can trip.
            r = await c.post(
                "/oauth/consent/request-link",
                data={"session": session, "email": "victim@example.com"},
                headers={"x-forwarded-for": f"10.0.0.{i}"},
            )
            results.append((r.status_code, "Check your email" in r.text))
    # Every response looks identical (200 + same page)…
    assert results == [(200, True), (200, True), (200, True)]
    # …but only the first actually sent a link.
    assert len(emailer.sent) == 1


# ---------------------------------------------------------------------------
# magic_get
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_magic_get_completes_oauth_and_binds_email(provider):
    session = await _new_session(provider)
    store = MagicLinkStore()
    token = store.issue(session, "user@example.com")
    app = _app(provider, email_gate=True, emailer=RecordingEmailer(), magic_store=store)
    async with _client(app) as c:
        r = await c.get(f"/oauth/magic?token={token}", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert "code" in q
    # The issued code carries the verified email through to the access token.
    from mcp.shared.auth import OAuthClientInformationFull

    client = OAuthClientInformationFull(client_id="c1", redirect_uris=None)
    auth_code = await provider.load_authorization_code(client, q["code"][0])
    assert auth_code is not None
    oauth_token = await provider.exchange_authorization_code(client, auth_code)
    assert provider.email_for_token(oauth_token.access_token) == "user@example.com"


@pytest.mark.anyio
async def test_magic_get_invalid_token(provider):
    app = _app(
        provider,
        email_gate=True,
        emailer=RecordingEmailer(),
        magic_store=MagicLinkStore(),
    )
    async with _client(app) as c:
        r = await c.get("/oauth/magic?token=nope")
    assert r.status_code == 400


@pytest.mark.anyio
async def test_magic_get_expired_session(provider):
    session = await _new_session(provider)
    store = MagicLinkStore()
    token = store.issue(session, "user@example.com")
    # Consume the session out from under the magic link (simulates expiry/use).
    provider.consume_session(session)
    app = _app(provider, email_gate=True, emailer=RecordingEmailer(), magic_store=store)
    async with _client(app) as c:
        r = await c.get(f"/oauth/magic?token={token}")
    assert r.status_code == 400
