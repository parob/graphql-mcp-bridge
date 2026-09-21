"""Environment-driven runtime configuration for Bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Tuple


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str_tuple(name: str, default: Tuple[str, ...]) -> Tuple[str, ...]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


# Credential headers seen across public GraphQL APIs: bearer/basic auth,
# generic API keys, and the vendor-specific ones users have actually hit the
# Bridge with (Radio France's x-token, Hasura's admin secret, Shopify's
# access token). Cookies cover session-authenticated APIs.
DEFAULT_FORWARD_HEADERS = ",".join([
    "authorization",
    "x-api-key",
    "api-key",
    "apikey",
    "x-token",
    "x-auth-token",
    "x-access-token",
    "x-hasura-admin-secret",
    "x-shopify-access-token",
    "cookie",
])


@dataclass(frozen=True)
class Settings:
    # Instance cache
    cache_maxsize: int = field(default_factory=lambda: _env_int("BRIDGE_CACHE_MAXSIZE", 1000))
    cache_ttl_seconds: int = field(default_factory=lambda: _env_int("BRIDGE_CACHE_TTL_SECONDS", 3600))

    # Upstream HTTP
    upstream_timeout_seconds: int = field(default_factory=lambda: _env_int("BRIDGE_UPSTREAM_TIMEOUT_SECONDS", 30))
    max_upstream_url_length: int = field(default_factory=lambda: _env_int("BRIDGE_MAX_UPSTREAM_URL_LENGTH", 2048))

    # Rate limiting (per client IP)
    rate_limit: str = field(default_factory=lambda: os.environ.get("BRIDGE_RATE_LIMIT", "60/minute"))

    # Header forwarding: the MCP client's headers are forwarded to the
    # upstream after a safe allowlist is applied. We forward the credential
    # headers GraphQL APIs commonly expect by default; everything else is
    # stripped. Users can broaden to "*" via BRIDGE_FORWARD_HEADERS="*",
    # but note the forwarded headers also feed the per-credential cache key,
    # so "*" makes every distinct client header set its own cache entry.
    forward_headers: Tuple[str, ...] | str = field(
        default_factory=lambda: (
            os.environ.get("BRIDGE_FORWARD_HEADERS", "")
            or DEFAULT_FORWARD_HEADERS
        )
    )

    # Admin
    admin_secret: str | None = field(default_factory=lambda: os.environ.get("BRIDGE_ADMIN_SECRET"))

    # Only set True for local testing — allows upstreams on loopback/private IPs.
    allow_internal_hosts: bool = field(
        default_factory=lambda: os.environ.get(
            "BRIDGE_ALLOW_INTERNAL_HOSTS", "").lower() in ("1", "true", "yes"))

    # User-Agent Bridge sends to upstream schema introspection + queries
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "BRIDGE_USER_AGENT",
            "graphql-mcp-bridge/0.1 (+https://graphql-mcp.com/bridge)",
        )
    )


def load_settings() -> Settings:
    return Settings()
