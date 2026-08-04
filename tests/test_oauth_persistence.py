"""Tests for durable OAuth state (SQLite) + sign-in audit log."""

from __future__ import annotations

import pytest

from meta_data_mcp.oauth_persistence import SqliteOAuthPersistence
from meta_data_mcp.oauth_provider import InMemoryOAuthProvider, compute_pkce_challenge


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "oauth.db")


def _client():
    from mcp.shared.auth import OAuthClientInformationFull

    return OAuthClientInformationFull(client_id="persist-test", redirect_uris=None)


async def _issue_token(provider, *, email):
    from mcp.server.auth.provider import AuthorizationParams

    client = _client()
    await provider.register_client(client)
    params = AuthorizationParams(
        state=None,
        scopes=["opendata"],
        code_challenge=compute_pkce_challenge("verifier123"),
        redirect_uri="http://localhost/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=False,
    )
    url = await provider.authorize(client, params)
    session = provider.consume_session(url.split("session=")[1])
    assert session is not None
    if email is not None:
        session["email"] = email
    code_str = provider.create_authorization_code(session)
    auth_code = await provider.load_authorization_code(client, code_str)
    return await provider.exchange_authorization_code(client, auth_code)


# ---------------------------------------------------------------------------
# Round-trip across a simulated restart (new provider, same DB)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_token_and_email_survive_restart(db_path):
    p1 = SqliteOAuthPersistence(db_path)
    provider1 = InMemoryOAuthProvider(
        issuer_url="http://localhost:8000",
        persistence=p1,
    )
    token = await _issue_token(provider1, email="user@example.com")
    p1.close()

    # "Restart": brand-new provider from the same DB.
    p2 = SqliteOAuthPersistence(db_path)
    provider2 = InMemoryOAuthProvider(
        issuer_url="http://localhost:8000",
        persistence=p2,
    )

    at = await provider2.verify_access_token(token.access_token)
    assert at is not None
    assert provider2.email_for_token(token.access_token) == "user@example.com"
    # The registered client came back too.
    assert await provider2.get_client("persist-test") is not None
    p2.close()


@pytest.mark.anyio
async def test_refresh_token_survives_restart(db_path):
    p1 = SqliteOAuthPersistence(db_path)
    provider1 = InMemoryOAuthProvider(
        issuer_url="http://localhost:8000",
        persistence=p1,
    )
    token = await _issue_token(provider1, email="user@example.com")
    p1.close()

    p2 = SqliteOAuthPersistence(db_path)
    provider2 = InMemoryOAuthProvider(
        issuer_url="http://localhost:8000",
        persistence=p2,
    )
    rt = await provider2.load_refresh_token(_client(), token.refresh_token)
    assert rt is not None
    new_token = await provider2.exchange_refresh_token(_client(), rt, scopes=[])
    assert provider2.email_for_token(new_token.access_token) == "user@example.com"
    p2.close()


@pytest.mark.anyio
async def test_revoke_removes_from_db(db_path):
    p1 = SqliteOAuthPersistence(db_path)
    provider1 = InMemoryOAuthProvider(
        issuer_url="http://localhost:8000",
        persistence=p1,
    )
    token = await _issue_token(provider1, email="user@example.com")
    at = await provider1.verify_access_token(token.access_token)
    assert at is not None
    await provider1.revoke_token(at)
    p1.close()

    p2 = SqliteOAuthPersistence(db_path)
    provider2 = InMemoryOAuthProvider(
        issuer_url="http://localhost:8000",
        persistence=p2,
    )
    assert await provider2.verify_access_token(token.access_token) is None
    p2.close()


# ---------------------------------------------------------------------------
# Sign-in audit log
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_signin_is_audited(db_path):
    p = SqliteOAuthPersistence(db_path)
    provider = InMemoryOAuthProvider(issuer_url="http://localhost:8000", persistence=p)
    await _issue_token(provider, email="auditor@example.com")

    events = p.recent_signins()
    assert len(events) == 1
    assert events[0].email == "auditor@example.com"
    assert events[0].client_id == "persist-test"
    assert events[0].ts > 0
    p.close()


@pytest.mark.anyio
async def test_no_email_means_no_signin_audit(db_path):
    p = SqliteOAuthPersistence(db_path)
    provider = InMemoryOAuthProvider(issuer_url="http://localhost:8000", persistence=p)
    await _issue_token(provider, email=None)
    assert p.recent_signins() == []
    p.close()


# ---------------------------------------------------------------------------
# Persistence layer unit round-trips
# ---------------------------------------------------------------------------


def test_persistence_client_roundtrip(db_path):
    from mcp.shared.auth import OAuthClientInformationFull

    p = SqliteOAuthPersistence(db_path)
    client = OAuthClientInformationFull(client_id="c1", redirect_uris=None)
    p.save_client(client)
    loaded = p.load_clients()
    assert "c1" in loaded
    assert loaded["c1"].client_id == "c1"
    p.close()


def test_expired_access_tokens_purged_on_startup(db_path):
    """Dead rows from expiry/rotation must not accumulate or reload on boot."""
    from mcp.server.auth.provider import AccessToken

    p1 = SqliteOAuthPersistence(db_path)
    expired = AccessToken(token="old", client_id="c1", scopes=[], expires_at=1)
    live = AccessToken(token="new", client_id="c1", scopes=[], expires_at=9999999999)
    p1.save_access_token("old", expired, "e@example.com")
    p1.save_access_token("new", live, "e@example.com")
    p1.close()

    p2 = SqliteOAuthPersistence(db_path)  # startup purge runs here
    tokens, _ = p2.load_access_tokens()
    assert "old" not in tokens
    assert "new" in tokens
    p2.close()


def test_db_parent_directory_is_created(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "oauth.db"
    p = SqliteOAuthPersistence(str(nested))  # must not raise
    assert nested.exists()
    p.close()


def test_persistence_access_token_roundtrip(db_path):
    from mcp.server.auth.provider import AccessToken

    p = SqliteOAuthPersistence(db_path)
    at = AccessToken(
        token="tok1",
        client_id="c1",
        scopes=["opendata"],
        expires_at=9999999999,
    )
    p.save_access_token("tok1", at, "e@example.com")
    tokens, emails = p.load_access_tokens()
    assert tokens["tok1"].client_id == "c1"
    assert emails["tok1"] == "e@example.com"
    p.delete_access_token("tok1")
    tokens2, _ = p.load_access_tokens()
    assert "tok1" not in tokens2
    p.close()
