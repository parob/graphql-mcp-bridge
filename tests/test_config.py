"""Settings defaults."""

from bridge.config import DEFAULT_FORWARD_HEADERS, Settings
from bridge.proxy import _normalize_forward_headers


def test_default_forward_headers_cover_common_credentials():
    headers = _normalize_forward_headers(Settings().forward_headers)
    assert headers == DEFAULT_FORWARD_HEADERS.split(",")
    for name in ("authorization", "x-api-key", "x-token", "cookie"):
        assert name in headers


def test_forward_headers_env_override(monkeypatch):
    monkeypatch.setenv("BRIDGE_FORWARD_HEADERS", " Authorization , X-Custom ")
    assert _normalize_forward_headers(Settings().forward_headers) == [
        "Authorization", "X-Custom"]
    monkeypatch.setenv("BRIDGE_FORWARD_HEADERS", "*")
    assert _normalize_forward_headers(Settings().forward_headers) == "*"
