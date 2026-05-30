"""Shared per-provider configuration dataclass.

A small, frozen dataclass that consolidates the knobs every provider
ends up declaring inline today (base URL, auth env var, content
negotiation default, rate-limit ceiling). Future work: have ``http_get``
read these directly instead of accepting them per-call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    """Static configuration for one provider module.

    Attributes:
        base_url: Root URL for the provider's API (no trailing slash).
        auth_env_var: If set, name of env var holding the API token.
        contact_required: True if the provider requires META_DATA_MCP_CONTACT
            to be set. ``http_get`` does not enforce this today; consult
            this flag in provider modules that need to fail fast.
        default_accept: Default Accept header for this provider's calls.
            Most providers serve JSON; override for SDMX/XML/plain endpoints.
        rate_limit_per_minute: Advisory rate-limit ceiling (calls/min).
            Not enforced by the kernel yet; used as documentation and
            future per-provider throttling input.
    """

    base_url: str
    auth_env_var: str | None = None
    contact_required: bool = False
    default_accept: str = "application/json"
    rate_limit_per_minute: int | None = None

    def __post_init__(self) -> None:
        """Normalize ``base_url`` by stripping any trailing slash."""
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def auth_headers(self) -> dict[str, str]:
        """Return an Authorization header dict if a token is available.

        Returns an empty dict when ``auth_env_var`` is unset, or when the
        environment variable is not set in the current process.
        """
        if not self.auth_env_var:
            return {}
        token = os.getenv(self.auth_env_var)
        if not token:
            return {}
        return {"Authorization": f"Token {token}"}


def require_env_key(var_name: str) -> str:
    """Return the value of environment variable *var_name* or raise ``RuntimeError``.

    Generated provider plugins must call this function (imported from
    ``meta_data_mcp.provider_config``) instead of calling ``os.getenv``
    directly.  Centralising the lookup here keeps ``os`` out of the
    generated-plugin import allowlist, which prevents a malicious or
    mis-generated plugin from enumerating or exfiltrating arbitrary
    environment variables.

    Raises:
        RuntimeError: If the variable is not set or is empty.
    """
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{var_name}' is not set. "
            "Configure it before starting the server."
        )
    return value
