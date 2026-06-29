"""Integration tests for the magic-link email gate.

The HTTP consent/magic handlers live in a ``run_server`` closure and are thin
glue over units tested elsewhere (emailer, MagicLinkStore). These tests cover
the two pieces with real logic that the glue depends on:

1. The OAuth provider threading a verified email session → code → token, so
   ``email_for_token`` can resolve a rate-limit identity.
2. ``BearerAuthMiddleware`` enforcing the per-user rate limit on the OAuth path
   while leaving the static-token path unthrottled.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from meta_data_mcp.auth_gate import RateLimiter
from meta_data_mcp.oauth_provider import InMemoryOAuthProvider, compute_pkce_challenge
from meta_data_mcp.server import BearerAuthMiddleware


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def provider():
    return InMemoryOAuthProvider(issuer_url="http://localhost:8000")


async def _issue_token(provider, *, email: str | None):
    """Drive the provider's authorize→code→exchange flow, optionally binding a
    verified email the way the magic-link handler does."""
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    client_info = OAuthClientInformationFull(client_id="gate-test", redirect_uris=None)
    params = AuthorizationParams(
        state=None,
        scopes=["opendata"],
        code_challenge=compute_pkce_challenge("verifier123"),
        redirect_uri="http://localhost/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=False,
    )
    url = await provider.authorize(client_info, params)
    session = provider.consume_session(url.split("session=")[1])
    assert session is not None
    if email is not None:
        session["email"] = email  # what magic_get does after verifying the link
    code_str = provider.create_authorization_code(session)
    auth_code = await provider.load_authorization_code(client_info, code_str)
    token = await provider.exchange_authorization_code(client_info, auth_code)
    return token


# ---------------------------------------------------------------------------
# Provider email binding: session → code → token
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verified_email_binds_to_issued_token(provider):
    token = await _issue_token(provider, email="user@example.com")
    assert provider.email_for_token(token.access_token) == "user@example.com"


@pytest.mark.anyio
async def test_no_email_means_no_binding(provider):
    token = await _issue_token(provider, email=None)
    assert provider.email_for_token(token.access_token) is None


@pytest.mark.anyio
async def test_revoke_clears_email_binding(provider):
    token = await _issue_token(provider, email="user@example.com")
    at = await provider.verify_access_token(token.access_token)
    assert at is not None
    await provider.revoke_token(at)
    assert provider.email_for_token(token.access_token) is None


@pytest.mark.anyio
async def test_refresh_preserves_email_binding(provider):
    """A refresh exchange must re-bind the email to the new access token, else
    a user could shed their rate-limit identity by refreshing."""
    from mcp.shared.auth import OAuthClientInformationFull

    token = await _issue_token(provider, email="user@example.com")
    client_info = OAuthClientInformationFull(client_id="gate-test", redirect_uris=None)
    rt = await provider.load_refresh_token(client_info, token.refresh_token)
    assert rt is not None
    new_token = await provider.exchange_refresh_token(client_info, rt, scopes=[])

    assert provider.email_for_token(new_token.access_token) == "user@example.com"
    # And the rotated refresh token carries it forward too.
    rt2 = await provider.load_refresh_token(client_info, new_token.refresh_token)
    assert rt2 is not None
    newer = await provider.exchange_refresh_token(client_info, rt2, scopes=[])
    assert provider.email_for_token(newer.access_token) == "user@example.com"


@pytest.mark.anyio
async def test_refresh_without_email_stays_unbound(provider):
    from mcp.shared.auth import OAuthClientInformationFull

    token = await _issue_token(provider, email=None)
    client_info = OAuthClientInformationFull(client_id="gate-test", redirect_uris=None)
    rt = await provider.load_refresh_token(client_info, token.refresh_token)
    assert rt is not None
    new_token = await provider.exchange_refresh_token(client_info, rt, scopes=[])
    assert provider.email_for_token(new_token.access_token) is None


# ---------------------------------------------------------------------------
# Middleware rate limiting
# ---------------------------------------------------------------------------


def _build_app(provider, *, static_token=None, rate_limiter=None):
    app = Starlette(routes=[Route("/sse", endpoint=lambda r: PlainTextResponse("ok"))])
    app.add_middleware(
        BearerAuthMiddleware,
        token=static_token,
        oauth_provider=provider,
        rate_limiter=rate_limiter,
    )
    return app


@pytest.mark.anyio
async def test_per_user_rate_limit_returns_429(provider):
    token = await _issue_token(provider, email="user@example.com")
    app = _build_app(provider, rate_limiter=RateLimiter(rpm=2, window_seconds=60))
    headers = {"Authorization": f"Bearer {token.access_token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        codes = [
            (await client.get("/sse", headers=headers)).status_code for _ in range(3)
        ]
        last = await client.get("/sse", headers=headers)

    assert codes == [200, 200, 429]
    assert last.status_code == 429
    assert int(last.headers["Retry-After"]) >= 0


@pytest.mark.anyio
async def test_rate_limit_is_per_email_not_per_token(provider):
    """Two tokens for the same verified email share one rate-limit bucket."""
    t1 = await _issue_token(provider, email="same@example.com")
    t2 = await _issue_token(provider, email="same@example.com")
    app = _build_app(provider, rate_limiter=RateLimiter(rpm=1, window_seconds=60))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.get(
            "/sse", headers={"Authorization": f"Bearer {t1.access_token}"}
        )
        r2 = await client.get(
            "/sse", headers={"Authorization": f"Bearer {t2.access_token}"}
        )

    assert r1.status_code == 200
    assert r2.status_code == 429  # second token, same email → throttled


@pytest.mark.anyio
async def test_static_token_not_rate_limited(provider):
    """The operator's static token bypasses the per-user throttle entirely."""
    app = _build_app(
        provider,
        static_token="operator-token",
        rate_limiter=RateLimiter(rpm=1, window_seconds=60),
    )
    headers = {"Authorization": "Bearer operator-token"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        codes = [
            (await client.get("/sse", headers=headers)).status_code for _ in range(5)
        ]

    assert codes == [200, 200, 200, 200, 200]


@pytest.mark.anyio
async def test_no_rate_limiter_means_no_throttle(provider):
    token = await _issue_token(provider, email="user@example.com")
    app = _build_app(provider, rate_limiter=None)
    headers = {"Authorization": f"Bearer {token.access_token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        codes = [
            (await client.get("/sse", headers=headers)).status_code for _ in range(5)
        ]

    assert codes == [200, 200, 200, 200, 200]
