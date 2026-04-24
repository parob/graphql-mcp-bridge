"""Upstream URL validation — blocks SSRF to internal networks."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UpstreamValidationError(ValueError):
    """The user-supplied upstream URL is invalid or points somewhere unsafe."""


def validate_upstream_url(
    raw: str,
    max_length: int = 2048,
    allow_internal_hosts: bool = False,
) -> str:
    """
    Validate and normalize an upstream GraphQL URL supplied by an MCP client.

    Raises UpstreamValidationError if:
      - the URL is malformed
      - the scheme isn't http/https
      - the host resolves to a non-global address (RFC1918, loopback,
        link-local, multicast, unspecified) — unless allow_internal_hosts
        is True, which is intended for local tests only.
      - the URL is longer than max_length

    Returns:
        The validated URL string (unchanged except for light normalization).
    """
    if not raw:
        raise UpstreamValidationError("upstream URL is empty")
    if len(raw) > max_length:
        raise UpstreamValidationError("upstream URL is too long")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise UpstreamValidationError(
            f"upstream scheme must be http or https, got {parsed.scheme!r}")
    if not parsed.hostname:
        raise UpstreamValidationError("upstream URL is missing a host")

    if not allow_internal_hosts:
        _ensure_public_host(parsed.hostname)
    return raw


def _ensure_public_host(host: str) -> None:
    """Resolve host to IP addresses and verify none of them are private."""
    # If host is already a literal IP, just check it directly.
    try:
        ip = ipaddress.ip_address(host)
        _assert_ip_is_public(ip, host)
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UpstreamValidationError(
            f"could not resolve upstream host {host!r}: {e}")

    for family, _kind, _proto, _canon, sockaddr in infos:
        raw_ip = sockaddr[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        _assert_ip_is_public(ip, host)


def _assert_ip_is_public(ip: ipaddress._BaseAddress, host: str) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UpstreamValidationError(
            f"upstream host {host!r} resolves to a non-public address ({ip})")
