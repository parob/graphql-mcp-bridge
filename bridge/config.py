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
    # upstream after a safe allowlist is applied. We forward authentication-
    # style headers by default; everything else is stripped.
    # Users can broaden to "*" via BRIDGE_FORWARD_HEADERS="*".
    forward_headers: Tuple[str, ...] | str = field(
        default_factory=lambda: (
            os.environ.get("BRIDGE_FORWARD_HEADERS", "")
            or "authorization,x-api-key,cookie"
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
