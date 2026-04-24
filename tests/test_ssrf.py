"""Tests for upstream URL validation (SSRF guard)."""

import pytest

from bridge.ssrf import UpstreamValidationError, validate_upstream_url


def test_valid_https_public_host():
    url = "https://countries.trevorblades.com/graphql"
    assert validate_upstream_url(url) == url


def test_rejects_empty():
    with pytest.raises(UpstreamValidationError):
        validate_upstream_url("")


def test_rejects_non_http_scheme():
    with pytest.raises(UpstreamValidationError):
        validate_upstream_url("file:///etc/passwd")
    with pytest.raises(UpstreamValidationError):
        validate_upstream_url("gopher://example.com")


def test_rejects_missing_host():
    with pytest.raises(UpstreamValidationError):
        validate_upstream_url("http:///graphql")


@pytest.mark.parametrize("bad", [
    "http://127.0.0.1/graphql",
    "http://localhost/graphql",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/graphql",
    "http://192.168.1.1/graphql",
    "http://[::1]/graphql",
])
def test_rejects_private_hosts(bad):
    with pytest.raises(UpstreamValidationError):
        validate_upstream_url(bad)


def test_rejects_too_long():
    long_url = "https://example.com/" + ("x" * 3000)
    with pytest.raises(UpstreamValidationError):
        validate_upstream_url(long_url, max_length=2048)
