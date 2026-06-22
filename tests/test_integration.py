"""End-to-end: Bridge in front of a real GraphQL upstream, hit via MCP client."""

import base64
import socket
import threading
import time
from contextlib import closing
from typing import cast
from urllib.parse import quote

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
