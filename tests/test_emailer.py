"""Tests for the magic-link emailer backend selection and delivery."""

from __future__ import annotations

import httpx
import pytest

from meta_data_mcp.emailer import (
    EmailBackend,
    Emailer,
    EmailMessage,
    magic_link_message,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# from_env backend selection
# ---------------------------------------------------------------------------


def _clear_email_env(monkeypatch):
    for var in (
        "META_DATA_MCP_RESEND_API_KEY",
        "META_DATA_MCP_SMTP_HOST",
        "META_DATA_MCP_EMAIL_FROM",
        "META_DATA_MCP_SMTP_PORT",
        "META_DATA_MCP_SMTP_USER",
        "META_DATA_MCP_SMTP_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)


def test_from_env_prefers_resend(monkeypatch):
    _clear_email_env(monkeypatch)
    monkeypatch.setenv("META_DATA_MCP_RESEND_API_KEY", "re_test")
    monkeypatch.setenv("META_DATA_MCP_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("META_DATA_MCP_EMAIL_FROM", "x@example.com")
    e = Emailer.from_env()
    assert e.backend is EmailBackend.RESEND


def test_from_env_smtp_when_no_resend(monkeypatch):
    _clear_email_env(monkeypatch)
    monkeypatch.setenv("META_DATA_MCP_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("META_DATA_MCP_SMTP_PORT", "2525")
    monkeypatch.setenv("META_DATA_MCP_EMAIL_FROM", "x@example.com")
    e = Emailer.from_env()
    assert e.backend is EmailBackend.SMTP
    assert e._smtp_port == 2525


def test_from_env_console_when_unconfigured(monkeypatch):
    _clear_email_env(monkeypatch)
    e = Emailer.from_env()
    assert e.backend is EmailBackend.CONSOLE


def test_from_env_resend_requires_from(monkeypatch):
    _clear_email_env(monkeypatch)
    monkeypatch.setenv("META_DATA_MCP_RESEND_API_KEY", "re_test")
    with pytest.raises(ValueError, match="EMAIL_FROM"):
        Emailer.from_env()


def test_from_env_smtp_requires_from(monkeypatch):
    _clear_email_env(monkeypatch)
    monkeypatch.setenv("META_DATA_MCP_SMTP_HOST", "smtp.example.com")
    with pytest.raises(ValueError, match="EMAIL_FROM"):
        Emailer.from_env()


def test_from_env_smtp_port_invalid_defaults(monkeypatch, caplog):
    _clear_email_env(monkeypatch)
    monkeypatch.setenv("META_DATA_MCP_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("META_DATA_MCP_EMAIL_FROM", "x@example.com")
    monkeypatch.setenv("META_DATA_MCP_SMTP_PORT", "not-a-number")
    with caplog.at_level("WARNING"):
        e = Emailer.from_env()
    assert e._smtp_port == 587  # fell back to default, did not crash
    assert "META_DATA_MCP_SMTP_PORT must be an integer" in caplog.text


# ---------------------------------------------------------------------------
# __init__ invariant validation
# ---------------------------------------------------------------------------


def test_init_resend_requires_key():
    with pytest.raises(ValueError, match="resend_api_key"):
        Emailer(EmailBackend.RESEND, from_addr="x@example.com")


def test_init_smtp_requires_host():
    with pytest.raises(ValueError, match="smtp_host"):
        Emailer(EmailBackend.SMTP, from_addr="x@example.com")


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_console_send_never_raises(caplog):
    e = Emailer(EmailBackend.CONSOLE, from_addr="x@example.com")
    with caplog.at_level("INFO"):
        await e.send(EmailMessage(to="u@example.com", subject="Hi", text="body"))
    assert any("email:console" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_resend_send_posts_and_succeeds():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "email_123"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    e = Emailer(
        EmailBackend.RESEND,
        from_addr="from@example.com",
        resend_api_key="re_test",
        http_client=client,
    )
    await e.send(
        EmailMessage(to="u@example.com", subject="Hi", text="body", html="<b>x</b>"),
    )
    await client.aclose()

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["to"] == ["u@example.com"]
    assert body["from"] == "from@example.com"
    assert body["html"] == "<b>x</b>"


@pytest.mark.anyio
async def test_resend_send_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "invalid sender"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    e = Emailer(
        EmailBackend.RESEND,
        from_addr="from@example.com",
        resend_api_key="re_test",
        http_client=client,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await e.send(EmailMessage(to="u@example.com", subject="Hi", text="body"))
    await client.aclose()


@pytest.mark.anyio
async def test_smtp_send_uses_starttls_and_login(monkeypatch):
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(f"connect:{host}:{port}")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            calls.append("starttls")

        def login(self, user, password):
            calls.append(f"login:{user}")

        def send_message(self, msg):
            calls.append(f"send:{msg['To']}")

    monkeypatch.setattr("meta_data_mcp.emailer.smtplib.SMTP", FakeSMTP)
    e = Emailer(
        EmailBackend.SMTP,
        from_addr="from@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
    )
    await e.send(EmailMessage(to="u@example.com", subject="Hi", text="body"))
    assert calls == [
        "connect:smtp.example.com:587",
        "starttls",
        "login:user",
        "send:u@example.com",
    ]


def test_magic_link_message_contains_link():
    msg = magic_link_message("u@example.com", "https://x/oauth/magic?token=abc")
    assert msg.to == "u@example.com"
    assert "https://x/oauth/magic?token=abc" in msg.text
    assert msg.html is not None
    assert "https://x/oauth/magic?token=abc" in msg.html
