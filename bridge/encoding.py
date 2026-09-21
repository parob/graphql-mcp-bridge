"""Decode the upstream-URL segment of a Bridge MCP path.

Bridge accepts two encodings transparently:

1. **Percent-encoded** (standard URL encoding) —
   ``/mcp/https%3A%2F%2Fapi.example.com%2Fgraphql``
2. **base64url** (RFC 4648 §5, URL-safe, padding optional) —
   ``/mcp/aHR0cHM6Ly9hcGkuZXhhbXBsZS5jb20vZ3JhcGhxbA``

base64url is nicer in JSON configs: no ``%`` characters to be double-
encoded by over-eager MCP clients, and significantly shorter than the
percent-encoded form for typical URLs.
"""

from __future__ import annotations

import base64
import binascii
from urllib.parse import unquote


def decode_upstream(raw: str) -> str:
    """Decode the upstream segment Bridge received in the request path.

    Tries percent-decoding first (covers raw URLs and standard encoding);
    if that doesn't yield something with an http(s) scheme, falls back to
    base64url decoding. Returns the (possibly still invalid) candidate URL
    in all cases — real validation is the SSRF guard's job.
    """
    if not raw:
        return raw

    # Surrounding whitespace is never part of a URL, but `echo url | base64`
    # (no -n) bakes a trailing newline into the token — seen in real traffic.
    url_decoded = unquote(raw).strip()
    if _looks_like_http_url(url_decoded):
        return url_decoded

    b64_decoded = _try_base64url(raw)
    if b64_decoded is not None:
        b64_decoded = b64_decoded.strip()
        if _looks_like_http_url(b64_decoded):
            return b64_decoded

    # Neither encoding produced something that looks right — hand the
    # percent-decoded value back; the SSRF/scheme check will reject it.
    return url_decoded


def _looks_like_http_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _try_base64url(raw: str) -> str | None:
    # base64url uses `-` and `_` in place of `+` and `/`; we also accept the
    # standard alphabet for robustness. Padding may be omitted.
    padding = "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode(raw + padding)
    except (binascii.Error, ValueError):
        try:
            decoded = base64.b64decode(raw + padding, validate=True)
        except (binascii.Error, ValueError):
            return None
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
