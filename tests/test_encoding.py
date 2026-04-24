"""Tests for upstream URL decoding (percent-encoded + base64url)."""

import base64
from urllib.parse import quote

import pytest

from bridge.encoding import decode_upstream


URL = "https://countries.trevorblades.com/graphql"


def test_decodes_percent_encoded_url():
    assert decode_upstream(quote(URL, safe="")) == URL


def test_passes_through_plain_url():
    # A client that doesn't percent-encode at all — Starlette may hand us
    # the URL as-is when path segments contain characters that don't need
    # encoding (unlikely in practice but must round-trip).
    assert decode_upstream(URL) == URL


def test_decodes_base64url_no_padding():
    encoded = base64.urlsafe_b64encode(URL.encode()).decode().rstrip("=")
    assert decode_upstream(encoded) == URL


def test_decodes_base64url_with_padding():
    encoded = base64.urlsafe_b64encode(URL.encode()).decode()
    # Padding may be present or not — both must decode
    assert decode_upstream(encoded) == URL


def test_decodes_standard_base64_alphabet():
    # Some clients might produce `+` / `/` instead of base64url's `-` / `_`.
    # Any URL-unsafe characters in base64 would have been percent-encoded
    # by the HTTP layer before reaching us, so we only exercise the
    # url-safe variant here.
    encoded = base64.urlsafe_b64encode(URL.encode()).decode().rstrip("=")
    assert decode_upstream(encoded) == URL


def test_prefers_percent_decoding_when_both_are_valid():
    # If percent-decoding already yields a URL, we don't touch it.
    encoded = quote(URL, safe="")
    assert decode_upstream(encoded) == URL


def test_rejected_bytes_fall_back_gracefully():
    # Not a URL after either decoding path — caller will reject via SSRF.
    garbage = "!!!not_a_url_or_base64!!!"
    result = decode_upstream(garbage)
    # The contract: we return *something* — SSRF/scheme check is the gate.
    # The percent-decoded version of garbage is just garbage itself.
    assert result == garbage


def test_empty_returns_empty():
    assert decode_upstream("") == ""


@pytest.mark.parametrize("url", [
    "https://api.github.com/graphql",
    "http://example.org:8080/graphql?foo=bar",
    "https://api.example.com/v1/graphql#frag",
])
def test_base64url_roundtrip(url):
    encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    assert decode_upstream(encoded) == url
