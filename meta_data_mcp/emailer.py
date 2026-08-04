"""Email delivery for the magic-link auth gate.

Provider-agnostic: the backend is selected from environment configuration at
construction time. No third-party SDKs — SMTP uses the stdlib, Resend uses the
already-vendored ``httpx``.

Backends (first match wins in :meth:`Emailer.from_env`):

1. **Resend** — ``META_DATA_MCP_RESEND_API_KEY`` set. POSTs to the Resend HTTP
   API (``https://api.resend.com/emails``).
2. **SMTP** — ``META_DATA_MCP_SMTP_HOST`` set. Sends via stdlib ``smtplib`` over
   STARTTLS. Honors ``META_DATA_MCP_SMTP_PORT`` (default 587),
   ``META_DATA_MCP_SMTP_USER``, ``META_DATA_MCP_SMTP_PASSWORD``.
3. **Console** — neither configured. Logs the message at INFO (dev/local
   default) so the magic-link flow is exercisable without an email provider.

Every backend shares the same async :meth:`Emailer.send` signature, so the gate
code never branches on provider. The sender address comes from
``META_DATA_MCP_EMAIL_FROM`` (required for Resend/SMTP; the console backend
falls back to a placeholder).
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as _StdEmailMessage
from enum import Enum

import anyio.to_thread
import httpx

log = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_DEFAULT_FROM = "meta-data-mcp <no-reply@localhost>"
_DEFAULT_SMTP_PORT = 587


def _int_env(name: str, default: int) -> int:
    """Parse an integer env var, warning and falling back on bad input.

    Mirrors the lenient parsing in ``InMemoryOAuthProvider`` so a typo in
    ``META_DATA_MCP_SMTP_PORT`` degrades to the default instead of crashing
    server startup.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s must be an integer; using default %d", name, default)
        return default


class EmailBackend(str, Enum):
    """Which delivery mechanism an :class:`Emailer` is wired to."""

    RESEND = "resend"
    SMTP = "smtp"
    CONSOLE = "console"


@dataclass(frozen=True)
class EmailMessage:
    """A single outbound email. ``html`` is optional; ``text`` is required so
    every message has a plaintext part for clients that don't render HTML.
    """

    to: str
    subject: str
    text: str
    html: str | None = None


class Emailer:
    """Sends email via a single configured backend.

    Construct with :meth:`from_env` in production; instantiate directly in tests
    to pin a backend without touching the environment.
    """

    def __init__(
        self,
        backend: EmailBackend,
        *,
        from_addr: str,
        resend_api_key: str | None = None,
        smtp_host: str | None = None,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # Validate backend-specific invariants up front so misconfiguration
        # fails loudly here instead of as a confusing runtime error later
        # (e.g. "Authorization: Bearer None" from Resend or a bare SMTP assert).
        if backend is EmailBackend.RESEND and not resend_api_key:
            raise ValueError("RESEND backend requires a resend_api_key")
        if backend is EmailBackend.SMTP and not smtp_host:
            raise ValueError("SMTP backend requires a smtp_host")

        self.backend = backend
        self.from_addr = from_addr
        self._resend_api_key = resend_api_key
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._http_client = http_client

    @classmethod
    def from_env(cls) -> Emailer:
        """Build an :class:`Emailer` from ``META_DATA_MCP_*`` environment vars.

        Backend precedence: Resend, then SMTP, then Console. The console
        fallback means a server with no email config still boots and the gate
        stays exercisable — the magic link is logged instead of mailed.
        """
        from_addr = os.getenv("META_DATA_MCP_EMAIL_FROM", "").strip()
        resend_key = os.getenv("META_DATA_MCP_RESEND_API_KEY", "").strip()
        smtp_host = os.getenv("META_DATA_MCP_SMTP_HOST", "").strip()

        if resend_key:
            if not from_addr:
                raise ValueError(
                    "META_DATA_MCP_RESEND_API_KEY is set but "
                    "META_DATA_MCP_EMAIL_FROM is not — Resend requires a "
                    "verified sender address.",
                )
            return cls(
                EmailBackend.RESEND,
                from_addr=from_addr,
                resend_api_key=resend_key,
            )

        if smtp_host:
            if not from_addr:
                raise ValueError(
                    "META_DATA_MCP_SMTP_HOST is set but META_DATA_MCP_EMAIL_FROM "
                    "is not — SMTP requires a sender address.",
                )
            return cls(
                EmailBackend.SMTP,
                from_addr=from_addr,
                smtp_host=smtp_host,
                smtp_port=_int_env("META_DATA_MCP_SMTP_PORT", _DEFAULT_SMTP_PORT),
                smtp_user=os.getenv("META_DATA_MCP_SMTP_USER") or None,
                smtp_password=os.getenv("META_DATA_MCP_SMTP_PASSWORD") or None,
            )

        log.warning(
            "No email backend configured (set META_DATA_MCP_RESEND_API_KEY or "
            "META_DATA_MCP_SMTP_HOST). Falling back to the console backend — "
            "magic links will be logged, not emailed. Do not use in production.",
        )
        return cls(EmailBackend.CONSOLE, from_addr=from_addr or _DEFAULT_FROM)

    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message`` via the configured backend.

        Raises on hard delivery failure (non-2xx from Resend, SMTP error) so
        the caller can surface "couldn't send — try again" rather than leaving
        the user waiting for a link that never arrives. The console backend
        never raises.
        """
        if self.backend is EmailBackend.RESEND:
            await self._send_resend(message)
        elif self.backend is EmailBackend.SMTP:
            await anyio.to_thread.run_sync(self._send_smtp, message)
        else:
            self._send_console(message)

    async def _send_resend(self, message: EmailMessage) -> None:
        payload: dict[str, object] = {
            "from": self.from_addr,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html
        headers = {"Authorization": f"Bearer {self._resend_api_key}"}

        if self._http_client is not None:
            resp = await self._http_client.post(
                RESEND_API_URL,
                json=payload,
                headers=headers,
            )
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(RESEND_API_URL, json=payload, headers=headers)
        resp.raise_for_status()

    def _send_smtp(self, message: EmailMessage) -> None:
        msg = _StdEmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")

        assert self._smtp_host is not None  # guaranteed by from_env
        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10.0) as smtp:
            smtp.starttls()
            if self._smtp_user and self._smtp_password:
                smtp.login(self._smtp_user, self._smtp_password)
            smtp.send_message(msg)

    def _send_console(self, message: EmailMessage) -> None:
        log.info(
            "[email:console] To=%s Subject=%r\n%s",
            message.to,
            message.subject,
            message.text,
        )


def magic_link_message(to: str, link: str) -> EmailMessage:
    """Build the magic-link sign-in email for the auth gate."""
    subject = "Your meta-data-mcp sign-in link"
    text = (
        "Click the link below to finish connecting to meta-data-mcp:\n\n"
        f"{link}\n\n"
        "This link is single-use and expires shortly. If you didn't request "
        "it, you can ignore this email."
    )
    html = (
        '<div style="font-family:sans-serif;max-width:480px">'
        "<h2>Finish connecting to meta-data-mcp</h2>"
        "<p>Click the button below to complete sign-in:</p>"
        f'<p><a href="{link}" '
        'style="display:inline-block;padding:.6rem 1.4rem;background:#2563eb;'
        'color:#fff;border-radius:4px;text-decoration:none">Sign in</a></p>'
        '<p style="color:#555;font-size:.9rem">This link is single-use and '
        "expires shortly. If you didn't request it, ignore this email.</p>"
        "</div>"
    )
    return EmailMessage(to=to, subject=subject, text=text, html=html)
