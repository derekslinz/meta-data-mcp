"""Tests for ``meta_data_mcp.errors``.

Verifies that:
- each :class:`ProviderError` subclass exposes sensible defaults
  (``kind``, ``retryable``, ``status``);
- :func:`translate_http_error` maps ``httpx`` exceptions onto the right
  subclass for representative status codes;
- :class:`RateLimitError` parses the ``Retry-After`` header into a float
  when possible and tolerates missing/malformed values;
- the rendered string form of an error never includes raw URLs.
"""

from __future__ import annotations

import httpx
import pytest

from meta_data_mcp.errors import (
    AuthError,
    BadRequestError,
    NetworkError,
    NotFoundError,
    ProviderError,
    RateLimitError,
    UpstreamError,
    translate_http_error,
)

PROVIDER = "test-provider"
SENSITIVE_URL = "https://example.com/sensitive?token=abc"


def _status_error(
    status: int,
    headers: dict[str, str] | None = None,
) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", SENSITIVE_URL)
    resp = httpx.Response(status_code=status, request=req, headers=headers or {})
    return httpx.HTTPStatusError(f"{status} from upstream", request=req, response=resp)


# ---------------------------------------------------------------------------
# Subclass defaults
# ---------------------------------------------------------------------------


def test_bad_request_defaults():
    err = BadRequestError(PROVIDER, "bad input")
    assert err.kind == "bad_request"
    assert err.retryable is False
    assert err.status == 400
    assert err.provider == PROVIDER


def test_bad_request_custom_status():
    err = BadRequestError(PROVIDER, "bad input", status=422)
    assert err.status == 422
    assert err.retryable is False


def test_not_found_defaults():
    err = NotFoundError(PROVIDER, "missing")
    assert err.kind == "not_found"
    assert err.retryable is False
    assert err.status == 404


def test_auth_defaults():
    err = AuthError(PROVIDER, "denied")
    assert err.kind == "auth"
    assert err.retryable is False
    assert err.status == 401


def test_rate_limit_defaults():
    err = RateLimitError(PROVIDER, "slow down")
    assert err.kind == "rate_limited"
    assert err.retryable is True
    assert err.status == 429
    assert err.retry_after is None


def test_upstream_defaults():
    err = UpstreamError(PROVIDER, "boom")
    assert err.kind == "upstream"
    assert err.retryable is True
    assert err.status == 500


def test_network_defaults():
    err = NetworkError(PROVIDER, "could not connect")
    assert err.kind == "network"
    assert err.retryable is True
    assert err.status is None


def test_base_provider_error_defaults():
    err = ProviderError(PROVIDER, "something else")
    assert err.kind == "provider_error"
    assert err.retryable is False
    assert err.status is None


# ---------------------------------------------------------------------------
# Translator: HTTP status mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 422])
def test_translate_bad_request(status):
    err = translate_http_error(PROVIDER, _status_error(status))
    assert isinstance(err, BadRequestError)
    assert err.status == status
    assert err.provider == PROVIDER
    assert err.retryable is False


def test_translate_not_found():
    err = translate_http_error(PROVIDER, _status_error(404))
    assert isinstance(err, NotFoundError)
    assert err.status == 404
    assert err.retryable is False


@pytest.mark.parametrize("status", [401, 403])
def test_translate_auth(status):
    err = translate_http_error(PROVIDER, _status_error(status))
    assert isinstance(err, AuthError)
    assert err.status == status
    assert err.retryable is False


def test_translate_rate_limit_with_header():
    err = translate_http_error(
        PROVIDER,
        _status_error(429, headers={"Retry-After": "2"}),
    )
    assert isinstance(err, RateLimitError)
    assert err.status == 429
    assert err.retryable is True
    assert err.retry_after == 2.0


def test_translate_rate_limit_missing_header():
    err = translate_http_error(PROVIDER, _status_error(429))
    assert isinstance(err, RateLimitError)
    assert err.retry_after is None


def test_translate_rate_limit_malformed_header():
    err = translate_http_error(
        PROVIDER,
        _status_error(429, headers={"Retry-After": "soon"}),
    )
    assert isinstance(err, RateLimitError)
    assert err.retry_after is None


@pytest.mark.parametrize("status", [500, 503, 599])
def test_translate_upstream(status):
    err = translate_http_error(PROVIDER, _status_error(status))
    assert isinstance(err, UpstreamError)
    assert err.status == status
    assert err.retryable is True


def test_translate_unexpected_status():
    # 418 isn't in any specific bucket — should fall back to base ProviderError.
    err = translate_http_error(PROVIDER, _status_error(418))
    assert type(err) is ProviderError
    assert err.status == 418


# ---------------------------------------------------------------------------
# Translator: non-status httpx errors
# ---------------------------------------------------------------------------


def test_translate_connect_error():
    cause = httpx.ConnectError("boom", request=httpx.Request("GET", SENSITIVE_URL))
    err = translate_http_error(PROVIDER, cause)
    assert isinstance(err, NetworkError)
    assert err.retryable is True
    assert err.__cause__ is cause


def test_translate_read_timeout():
    cause = httpx.ReadTimeout("slow", request=httpx.Request("GET", SENSITIVE_URL))
    err = translate_http_error(PROVIDER, cause)
    assert isinstance(err, NetworkError)


def test_translate_generic_exception_returns_base():
    cause = RuntimeError("not http at all")
    err = translate_http_error(PROVIDER, cause)
    assert type(err) is ProviderError
    assert err.kind == "provider_error"
    assert err.__cause__ is cause


def test_translate_provider_error_passthrough():
    original = NotFoundError(PROVIDER, "already translated")
    translated = translate_http_error(PROVIDER, original)
    assert translated is original


# ---------------------------------------------------------------------------
# String form: must not leak URLs / sensitive request data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        _status_error(404),
        _status_error(429, headers={"Retry-After": "1"}),
        _status_error(500),
        _status_error(400),
        _status_error(401),
        httpx.ConnectError("boom", request=httpx.Request("GET", SENSITIVE_URL)),
    ],
)
def test_str_form_does_not_leak_url(exc):
    err = translate_http_error(PROVIDER, exc)
    rendered = str(err)
    assert "example.com" not in rendered
    assert "token=abc" not in rendered
    assert "sensitive" not in rendered
    # Provider scope and kind label should be present though.
    assert PROVIDER in rendered
    assert err.kind in rendered
