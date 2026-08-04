"""Provider error hierarchy and HTTP-to-domain translator.

This module defines a small, stable exception hierarchy used by providers
to surface upstream failures in a way that is safe to forward to LLM
clients. Subclasses carry a provider id, a coarse ``kind`` label, a
``retryable`` flag, an optional HTTP status, and the originating
exception (preserved via ``__cause__``).

Error messages produced by :func:`translate_http_error` are deliberately
free of raw URLs and other request-specific identifiers so that the
rendered string form can be returned to callers without leaking
endpoints, query parameters, or credentials.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for translated provider failures.

    Carries provider id, kind label, retryable flag, optional HTTP status,
    and the originating exception. The string form is stable for caller
    handling; URL-safety is guaranteed for messages produced by
    ``translate_http_error``.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        kind: str = "provider_error",
        retryable: bool = False,
        status: int | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.kind = kind
        self.retryable = retryable
        self.status = status
        if cause is not None:
            self.__cause__ = cause

    def __str__(self) -> str:
        bits = [f"[{self.provider}] {self.kind}"]
        if self.status is not None:
            bits.append(f"status={self.status}")
        bits.append(self.args[0] if self.args else "")
        return " ".join(b for b in bits if b)


class BadRequestError(ProviderError):
    """400/422 — caller-supplied input was rejected. Not retryable."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int = 400,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            provider,
            message,
            kind="bad_request",
            retryable=False,
            status=status,
            cause=cause,
        )


class NotFoundError(ProviderError):
    """404 — resource not present. Not retryable."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int = 404,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            provider,
            message,
            kind="not_found",
            retryable=False,
            status=status,
            cause=cause,
        )


class AuthError(ProviderError):
    """401/403 — authentication/authorization failure. Not retryable."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int = 401,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            provider,
            message,
            kind="auth",
            retryable=False,
            status=status,
            cause=cause,
        )


class RateLimitError(ProviderError):
    """429 — caller is being rate-limited. Retryable after backoff."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int = 429,
        retry_after: float | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            provider,
            message,
            kind="rate_limited",
            retryable=True,
            status=status,
            cause=cause,
        )
        self.retry_after = retry_after  # float seconds or None


class UpstreamError(ProviderError):
    """5xx — remote service issue. Retryable."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int = 500,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            provider,
            message,
            kind="upstream",
            retryable=True,
            status=status,
            cause=cause,
        )


class NetworkError(ProviderError):
    """Connect/read/timeout failure before a response was received. Retryable."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            provider,
            message,
            kind="network",
            retryable=True,
            status=None,
            cause=cause,
        )


def translate_http_error(provider: str, exc: Exception) -> ProviderError:
    """Map an httpx exception to the appropriate ProviderError subclass.

    Crucially: error messages produced here MUST NOT include raw URLs —
    the LLM client should see a stable, provider-scoped message.
    """
    if isinstance(exc, ProviderError):
        return exc

    import httpx  # local import to keep cold-start cost low

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (400, 422):
            return BadRequestError(
                provider,
                f"upstream rejected request (HTTP {status})",
                status=status,
                cause=exc,
            )
        if status == 404:
            return NotFoundError(
                provider,
                "resource not found",
                status=status,
                cause=exc,
            )
        if status in (401, 403):
            return AuthError(
                provider,
                "authentication or authorization failed",
                status=status,
                cause=exc,
            )
        if status == 429:
            retry_after = exc.response.headers.get("Retry-After")
            retry_seconds: float | None = None
            if retry_after:
                try:
                    retry_seconds = float(retry_after)
                except ValueError:
                    retry_seconds = None
            return RateLimitError(
                provider,
                "rate limited by upstream",
                retry_after=retry_seconds,
                status=status,
                cause=exc,
            )
        if 500 <= status < 600:
            return UpstreamError(
                provider,
                f"upstream service error (HTTP {status})",
                status=status,
                cause=exc,
            )
        return ProviderError(
            provider,
            f"unexpected HTTP status {status}",
            status=status,
            cause=exc,
        )
    if isinstance(exc, httpx.RequestError):
        return NetworkError(provider, "network failure contacting upstream", cause=exc)
    return ProviderError(provider, "unexpected provider error", cause=exc)
