"""Tests for OAuth 2.0 support in meta-data-mcp.

Covers:
- InMemoryOAuthProvider: client registration, consent flow, code exchange,
  token verification, refresh token rotation.
- Server routes: /.well-known/oauth-authorization-server, /register, and
  the exemption of OAuth routes from BearerAuthMiddleware.
- BearerAuthMiddleware: coexistence of static bearer token and OAuth tokens.
"""

from __future__ import annotations

import secrets

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from meta_data_mcp.oauth_provider import InMemoryOAuthProvider, compute_pkce_challenge
from meta_data_mcp.server import BearerAuthMiddleware


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def provider():
    return InMemoryOAuthProvider(issuer_url="http://localhost:8000")


# ---------------------------------------------------------------------------
# InMemoryOAuthProvider unit tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_and_get_client(provider):
    """register_client stores the client; get_client retrieves it by id."""
    from mcp.shared.auth import OAuthClientInformationFull

    client = OAuthClientInformationFull(
        client_id="test-client-1",
        client_secret="s3cr3t",
        redirect_uris=["http://localhost:3000/callback"],  # type: ignore[arg-type]
        grant_types=["authorization_code", "refresh_token"],
    )
    await provider.register_client(client)
    retrieved = await provider.get_client("test-client-1")
    assert retrieved is not None
    assert retrieved.client_id == "test-client-1"


@pytest.mark.anyio
async def test_get_client_unknown_returns_none(provider):
    assert await provider.get_client("does-not-exist") is None


@pytest.mark.anyio
async def test_authorize_returns_consent_url(provider):
    """authorize() returns a /oauth/consent URL with a session token."""
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    client = OAuthClientInformationFull(
        client_id="c1",
        redirect_uris=["http://localhost:3000/cb"],  # type: ignore[arg-type]
    )
    params = AuthorizationParams(
        state="my-state",
        scopes=["opendata"],
        code_challenge="abc123",
        redirect_uri="http://localhost:3000/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    assert url.startswith("http://localhost:8000/oauth/consent?session=")


@pytest.mark.anyio
async def test_consume_session_one_shot(provider):
    """consume_session returns the session once, then None."""
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    client = OAuthClientInformationFull(client_id="c1", redirect_uris=None)
    params = AuthorizationParams(
        state=None,
        scopes=[],
        code_challenge="x",
        redirect_uri="http://localhost/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=False,
    )
    url = await provider.authorize(client, params)
    token = url.split("session=")[1]

    session = provider.consume_session(token)
    assert session is not None
    # Second consumption returns None
    assert provider.consume_session(token) is None


@pytest.mark.anyio
async def test_full_authorization_code_flow(provider):
    """Full PKCE flow: register → authorize → create_code → exchange → verify."""
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    # 1. Register client
    client = OAuthClientInformationFull(
        client_id="pkce-client",
        redirect_uris=["http://localhost:3000/cb"],  # type: ignore[arg-type]
    )
    await provider.register_client(client)

    # 2. Build PKCE
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = compute_pkce_challenge(code_verifier)

    # 3. Authorize → consent URL
    params = AuthorizationParams(
        state="s1",
        scopes=["opendata"],
        code_challenge=code_challenge,
        redirect_uri="http://localhost:3000/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    session_token = url.split("session=")[1]

    # 4. Simulate consent approval → auth code
    session = provider.consume_session(session_token)
    assert session is not None
    code_str = provider.create_authorization_code(session)

    # 5. Load and exchange the code
    auth_code = await provider.load_authorization_code(client, code_str)
    assert auth_code is not None
    assert auth_code.code_challenge == code_challenge

    token = await provider.exchange_authorization_code(client, auth_code)
    assert token.access_token
    assert token.refresh_token

    # 6. Verify the access token
    at = await provider.verify_access_token(token.access_token)
    assert at is not None
    assert at.client_id == "pkce-client"

    # 7. Code is consumed (one-shot)
    assert await provider.load_authorization_code(client, code_str) is None


@pytest.mark.anyio
async def test_refresh_token_rotation(provider):
    """exchange_refresh_token issues new tokens and invalidates the old refresh token."""
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    client = OAuthClientInformationFull(client_id="rc", redirect_uris=None)
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = compute_pkce_challenge(code_verifier)
    params = AuthorizationParams(
        state=None,
        scopes=["opendata"],
        code_challenge=code_challenge,
        redirect_uri="http://localhost/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=False,
    )
    url = await provider.authorize(client, params)
    session = provider.consume_session(url.split("session=")[1])
    assert session is not None
    code_str = provider.create_authorization_code(session)
    auth_code = await provider.load_authorization_code(client, code_str)
    original = await provider.exchange_authorization_code(client, auth_code)

    rt = await provider.load_refresh_token(client, original.refresh_token)
    refreshed = await provider.exchange_refresh_token(client, rt, [])

    assert refreshed.access_token != original.access_token
    assert refreshed.refresh_token != original.refresh_token
    # Old refresh token is gone
    assert await provider.load_refresh_token(client, original.refresh_token) is None


@pytest.mark.anyio
async def test_verify_access_token_unknown_returns_none(provider):
    assert await provider.verify_access_token("unknown-token") is None


# ---------------------------------------------------------------------------
# BearerAuthMiddleware — OAuth coexistence
# ---------------------------------------------------------------------------


def _build_dual_auth_app(static_token: str | None, oauth_provider_instance=None):
    """Build a test Starlette app with dual-auth middleware."""
    app = Starlette(
        routes=[
            Route(
                "/sse",
                endpoint=lambda r: PlainTextResponse("sse-ok"),
                methods=["GET"],
            ),
            Route(
                "/",
                endpoint=lambda r: PlainTextResponse("root-ok"),
                methods=["GET"],
            ),
        ]
    )
    app.add_middleware(
        BearerAuthMiddleware,
        token=static_token,
        oauth_provider=oauth_provider_instance,
    )
    return app


@pytest.mark.anyio
async def test_static_token_still_accepted_when_oauth_enabled(provider):
    """The static bearer token continues to work when OAuth is also configured."""
    app = _build_dual_auth_app("my-static-token", oauth_provider_instance=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/sse", headers={"Authorization": "Bearer my-static-token"}
        )
    assert r.status_code == 200


@pytest.mark.anyio
async def test_oauth_token_accepted_by_middleware(provider):
    """An OAuth-issued access token is accepted by BearerAuthMiddleware."""
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull

    # Issue a real OAuth token
    client_info = OAuthClientInformationFull(client_id="mw-test", redirect_uris=None)
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
    code_str = provider.create_authorization_code(session)
    auth_code = await provider.load_authorization_code(client_info, code_str)
    oauth_token = await provider.exchange_authorization_code(client_info, auth_code)

    app = _build_dual_auth_app(static_token=None, oauth_provider_instance=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/sse",
            headers={"Authorization": f"Bearer {oauth_token.access_token}"},
        )
    assert r.status_code == 200


@pytest.mark.anyio
async def test_invalid_token_rejected(provider):
    app = _build_dual_auth_app("real-token", oauth_provider_instance=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/sse", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Server OAuth routes via Starlette (ISC-24, -25, -26)
# ---------------------------------------------------------------------------


def _build_oauth_starlette_app(issuer: str = "http://localhost:8000"):
    """Build a minimal Starlette app with OAuth routes mounted."""
    from mcp.server.auth.routes import (
        create_auth_routes,
        create_protected_resource_routes,
    )
    from mcp.server.auth.settings import ClientRegistrationOptions
    from pydantic import AnyHttpUrl
    from starlette.responses import HTMLResponse
    from starlette.requests import Request

    p = InMemoryOAuthProvider(issuer_url=issuer)

    async def consent_get(request: Request):
        return HTMLResponse("<form>consent</form>")

    async def consent_post(request: Request):
        return HTMLResponse("approved")

    protected_resource_routes = create_protected_resource_routes(
        resource_url=AnyHttpUrl(issuer),
        authorization_servers=[AnyHttpUrl(issuer)],
        scopes_supported=["opendata"],
    )
    oauth_routes = create_auth_routes(
        provider=p,
        issuer_url=AnyHttpUrl(issuer),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["opendata"],
            default_scopes=["opendata"],
        ),
    )

    all_routes = (
        protected_resource_routes
        + oauth_routes
        + [
            Route("/oauth/consent", endpoint=consent_get, methods=["GET"]),
            Route("/oauth/consent/approve", endpoint=consent_post, methods=["POST"]),
            Route("/sse", endpoint=lambda r: HTMLResponse("sse-ok"), methods=["GET"]),
        ]
    )

    app = Starlette(routes=all_routes)
    app.add_middleware(BearerAuthMiddleware, token="static-token", oauth_provider=p)
    return app, p


@pytest.mark.anyio
async def test_oauth_metadata_endpoint():
    """/.well-known/oauth-authorization-server returns 200 + issuer field."""
    app, _ = _build_oauth_starlette_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        r = await client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    data = r.json()
    assert data["issuer"].rstrip("/") == "http://localhost:8000"
    assert "token_endpoint" in data
    assert "registration_endpoint" in data


@pytest.mark.anyio
async def test_oauth_dynamic_registration():
    """POST /register returns client_id."""
    app, _ = _build_oauth_starlette_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        r = await client.post(
            "/register",
            json={
                "client_name": "Test Client",
                "redirect_uris": ["http://localhost:3000/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
    assert r.status_code == 201
    data = r.json()
    assert "client_id" in data


@pytest.mark.anyio
async def test_oauth_routes_exempt_from_bearer_middleware():
    """OAuth discovery, register, and consent routes return non-401 without auth."""
    app, _ = _build_oauth_starlette_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        # metadata endpoint — no auth
        r1 = await client.get("/.well-known/oauth-authorization-server")
        assert r1.status_code != 401, "metadata endpoint should not require auth"

        # consent page — no auth
        r2 = await client.get("/oauth/consent?session=dummy")
        # 200 (form) or 400 (invalid session) — not 401
        assert r2.status_code != 401, "consent page should not require auth"

        # /sse without auth → 401 (protected)
        r3 = await client.get("/sse")
        assert r3.status_code == 401


@pytest.mark.anyio
async def test_protected_resource_metadata_endpoint():
    """/.well-known/oauth-protected-resource returns 200 + required RFC 9728 fields."""
    app, _ = _build_oauth_starlette_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        r = await client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    data = r.json()
    assert "localhost:8000" in data.get(
        "resource", ""
    ), "resource must be the protected resource URL"
    assert "authorization_servers" in data
    auth_servers = data["authorization_servers"]
    assert any("localhost:8000" in s for s in auth_servers)


@pytest.mark.anyio
async def test_protected_resource_endpoint_no_auth_required():
    """/.well-known/oauth-protected-resource is accessible without Bearer auth."""
    app, _ = _build_oauth_starlette_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        r = await client.get("/.well-known/oauth-protected-resource")
    assert r.status_code != 401, "discovery endpoint must not require authentication"
