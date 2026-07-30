import pytest

from meta_data_mcp import contribute_consent

# NB: the mock sessions below define `check_client_capability` as a plain
# `def`, matching the real mcp SDK (it returns bool synchronously). Do NOT make
# it `async` — that would let a re-introduced `await` on the production call
# pass tests while silently killing the consent prompt. test_decline_elicitation
# is the regression guard: with a sync mock, an accidental `await` raises
# TypeError, is swallowed to "proceed", and that test flips to failing.


@pytest.mark.asyncio
async def test_disabled_when_env_off(monkeypatch):
    monkeypatch.setenv("META_DATA_MCP_AUTO_CONTRIBUTE", "0")
    assert await contribute_consent.resolve_consent("acme") == "disabled"


@pytest.mark.asyncio
async def test_proceed_when_no_request_context(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)
    # No active MCP request context in a plain test → cannot ask → proceed.
    monkeypatch.setattr(contribute_consent, "_current_session", lambda: None)
    assert await contribute_consent.resolve_consent("acme") == "proceed"


@pytest.mark.asyncio
async def test_proceed_when_client_lacks_elicitation(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)

    class Sess:
        def check_client_capability(self, cap):
            return False

    monkeypatch.setattr(contribute_consent, "_current_session", lambda: Sess())
    assert await contribute_consent.resolve_consent("acme") == "proceed"


@pytest.mark.asyncio
async def test_accept_elicitation_proceeds(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)

    class Result:
        action = "accept"
        content = {"contribute": True}

    class Sess:
        def check_client_capability(self, cap):
            return True

        async def elicit(self, message, requestedSchema, related_request_id=None):
            return Result()

    monkeypatch.setattr(contribute_consent, "_current_session", lambda: Sess())
    assert await contribute_consent.resolve_consent("acme") == "proceed"


@pytest.mark.asyncio
async def test_decline_elicitation_declines(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)

    class Result:
        action = "decline"
        content = None

    class Sess:
        def check_client_capability(self, cap):
            return True

        async def elicit(self, message, requestedSchema, related_request_id=None):
            return Result()

    monkeypatch.setattr(contribute_consent, "_current_session", lambda: Sess())
    assert await contribute_consent.resolve_consent("acme") == "declined"
