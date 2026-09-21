"""End-to-end: Bridge in front of a real GraphQL upstream, hit via MCP client."""

import base64
import socket
import threading
import time
from contextlib import closing
from typing import cast
from urllib.parse import quote

import httpx
import pytest
import uvicorn
from fastmcp.client import Client
from mcp.types import TextContent

from bridge.app import create_app
from bridge.config import Settings


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _result_text(result) -> str:
    content = result.content if hasattr(result, "content") else result
    return cast(TextContent, content[0]).text


@pytest.fixture(scope="module")
def bridge_server(upstream_graphql):  # noqa: ARG001 — fixture kept alive
    port = _free_port()
    settings = Settings(allow_internal_hosts=True)
    app = create_app(settings=settings)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("bridge failed to start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_bridge_exposes_upstream_tools(upstream_graphql, bridge_server):
    mcp_url = f"{bridge_server}/mcp/{quote(upstream_graphql, safe='')}"
    async with Client(mcp_url) as client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert "hello" in tool_names
        assert "whoami" in tool_names

        result = await client.call_tool("hello", {"name": "Bridge"})
        assert _result_text(result) == "Hello, Bridge!"


@pytest.mark.asyncio
async def test_bridge_handles_reserved_keyword_argument(
        upstream_graphql, bridge_server):
    """Regression for graphql-mcp issue #5: an upstream field whose argument is
    a Python reserved keyword (`from`) must be exposed (as `from_`) and callable
    through the bridge, round-tripping back to the real GraphQL name."""
    mcp_url = f"{bridge_server}/mcp/{quote(upstream_graphql, safe='')}"
    async with Client(mcp_url) as client:
        tools = await client.list_tools()
        convert = next(t for t in tools if t.name == "convert")
        # The reserved keyword is surfaced to MCP clients with a trailing _.
        params = set(convert.inputSchema.get("properties", {}))
        assert "from_" in params
        assert "from" not in params

        result = await client.call_tool(
            "convert", {"from_": "UNIPROT", "to": "PDB_ENTITY"})
    # Upstream received the real `from` arg and echoed it back.
    assert _result_text(result) == "UNIPROT->PDB_ENTITY"


@pytest.mark.asyncio
async def test_root_serves_landing_to_browser(bridge_server):
    """The bare host serves the explainer + URL-mapper landing page to browsers,
    but still serves the JSON service-info response to API/programmatic clients."""
    async with httpx.AsyncClient(base_url=bridge_server, timeout=30) as c:
        # Browser (Accept: text/html) → the landing page with the URL mapper.
        r = await c.get("/", headers={"Accept": "text/html"})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "GraphQL MCP Bridge" in r.text
        assert "toBase64Url" in r.text  # the URL mapper logic is present
        assert 'id="upstream"' in r.text and 'id="output"' in r.text

        # API client (no text/html) → JSON, linking the docs URL.
        r = await c.get("/")
        assert r.status_code == 200
        assert r.json()["docs"] == "https://graphql-mcp.parob.com/bridge"


@pytest.mark.asyncio
async def test_bridge_serves_graphiql_explorer(upstream_graphql, bridge_server):
    """The Bridge serves graphql-mcp's GraphiQL + MCP plugin at /graphql,
    proxies GraphQL to the upstream, and shows a browser visiting the bare URL
    an explainer with a button to it — without disturbing the MCP endpoint."""
    tok = quote(upstream_graphql, safe="")
    async with httpx.AsyncClient(base_url=bridge_server, timeout=30) as c:
        # A browser hitting the bare MCP URL gets the explainer page with a
        # button to the explorer — and no auto-redirect.
        r = await c.get(f"/mcp/{tok}", headers={"Accept": "text/html"})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert f"/mcp/{tok}/graphql" in r.text  # button links to the explorer
        # The suggested MCP endpoint is the full absolute URL, not a path.
        assert f"{bridge_server}/mcp/{tok}" in r.text
        assert "http-equiv" not in r.text  # no meta-refresh auto-redirect

        # The explorer renders the GraphiQL page (with the MCP plugin), and
        # declares the canonical MCP endpoint so the plugin targets it instead
        # of guessing "<explorer path>/mcp".
        r = await c.get(f"/mcp/{tok}/graphql", headers={"Accept": "text/html"})
        assert r.status_code == 200
        assert "graphiql" in r.text.lower()
        assert (f'window.__GRAPHQL_MCP_URL__="{bridge_server}/mcp/{tok}";'
                in r.text)  # the bare token, no /graphql or /mcp suffix
        assert int(r.headers["content-length"]) == len(r.content)

        # GraphQL queries posted to the explorer are proxied to the upstream,
        # and the JSON response is passed through without any injection.
        r = await c.post(f"/mcp/{tok}/graphql", json={"query": "{ hello }"})
        assert r.status_code == 200
        assert r.json()["data"]["hello"] == "Hello, World!"
        assert "__GRAPHQL_MCP_URL__" not in r.text


@pytest.mark.asyncio
async def test_bridge_mcp_still_served_via_plugin_path(
        upstream_graphql, bridge_server):
    """The MCP endpoint the GraphiQL plugin derives (<explorer>/mcp) reaches
    the same MCP server as the bare URL."""
    tok = quote(upstream_graphql, safe="")
    mcp_url = f"{bridge_server}/mcp/{tok}/graphql/mcp"
    async with Client(mcp_url) as client:
        tools = {t.name for t in await client.list_tools()}
        assert "hello" in tools
        result = await client.call_tool("hello", {"name": "Plugin"})
        assert _result_text(result) == "Hello, Plugin!"


@pytest.mark.asyncio
async def test_bridge_accepts_base64url_upstream(upstream_graphql, bridge_server):
    b64 = base64.urlsafe_b64encode(upstream_graphql.encode()).decode().rstrip("=")
    mcp_url = f"{bridge_server}/mcp/{b64}"
    async with Client(mcp_url) as client:
        tools = await client.list_tools()
        assert "hello" in {t.name for t in tools}
        result = await client.call_tool("hello", {"name": "base64"})
    assert _result_text(result) == "Hello, base64!"


@pytest.mark.asyncio
async def test_bridge_forwards_authorization_header(upstream_graphql, bridge_server):
    mcp_url = f"{bridge_server}/mcp/{quote(upstream_graphql, safe='')}"
    # fastmcp's `auth=<token>` sends `Authorization: Bearer <token>`.
    # Bridge's default allowlist forwards Authorization to the upstream,
    # and the upstream's whoami() reflects that header back.
    async with Client(mcp_url, auth="secret-token") as client:
        result = await client.call_tool("whoami", {})
    assert _result_text(result) == "Bearer secret-token"


@pytest.mark.asyncio
async def test_bridge_rejects_private_upstream(bridge_server):
    # Without BRIDGE_ALLOW_INTERNAL_HOSTS, localhost is denied. Our fixture
    # enables it — so construct a fresh Bridge without the escape hatch.
    port = _free_port()
    settings = Settings(allow_internal_hosts=False, rate_limit="1000/minute")
    app = create_app(settings=settings)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("bridge failed to start")

    try:
        import httpx
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"http://127.0.0.1:{port}/mcp/"
                + quote("http://127.0.0.1:9999/graphql", safe=""),
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            )
        assert resp.status_code == 400
        assert "non-public" in resp.text or "invalid upstream" in resp.text
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_introspection_uses_callers_credentials(
    upstream_graphql_auth, bridge_server,
):
    """An upstream that requires auth even for introspection works when the
    caller sends the credentials the bridge forwards; without them the
    bridge reports the upstream's refusal instead of a shared instance."""
    mcp_url = f"{bridge_server}/mcp/{quote(upstream_graphql_auth, safe='')}"

    async with Client(mcp_url, auth="secret-token") as client:
        tools = {t.name for t in await client.list_tools()}
        assert "whoami" in tools
        result = await client.call_tool("whoami", {})
    assert _result_text(result) == "Bearer secret-token"

    # The instance built above must not be handed to an unauthenticated
    # caller: the cache is keyed per credential set.
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            mcp_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert resp.status_code == 502
    assert "401" in resp.json()["error"]
