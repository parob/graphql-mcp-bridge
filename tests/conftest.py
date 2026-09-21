"""Shared test fixtures."""

import socket
import threading
import time
from contextlib import closing

import pytest
import uvicorn
from graphql import (
    GraphQLArgument,
    GraphQLField,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
    graphql_sync,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def _build_demo_schema() -> GraphQLSchema:
    def resolve_hello(_root, _info, name="World"):
        return f"Hello, {name}!"

    def resolve_whoami(_root, info, **_kwargs):
        # Reflect what we saw in the Authorization header back to the caller.
        http_headers = info.context.get("http_headers", {}) if info.context else {}
        return http_headers.get("authorization", "")

    def resolve_convert(_root, _info, **kwargs):
        # `from` is a Python keyword, so graphql-core delivers it via **kwargs.
        # Echo it back to prove the bridge sent the real GraphQL arg name.
        return f"{kwargs.get('from')}->{kwargs.get('to')}"

    return GraphQLSchema(
        query=GraphQLObjectType(
            "Query",
            fields={
                "hello": GraphQLField(
                    GraphQLNonNull(GraphQLString),
                    args={"name": GraphQLArgument(GraphQLString)},
                    resolve=resolve_hello,
                ),
                "whoami": GraphQLField(
                    GraphQLNonNull(GraphQLString),
                    resolve=resolve_whoami,
                ),
                # Field with a Python reserved keyword (`from`) as an argument
                # name — exercises graphql-mcp issue #5 through the bridge.
                "convert": GraphQLField(
                    GraphQLNonNull(GraphQLString),
                    args={
                        "from": GraphQLArgument(GraphQLNonNull(GraphQLString)),
                        "to": GraphQLArgument(GraphQLNonNull(GraphQLString)),
                    },
                    resolve=resolve_convert,
                ),
            },
        )
    )


def _make_upstream_app(schema: GraphQLSchema) -> Starlette:
    async def graphql_endpoint(request: Request):
        body = await request.json()
        query = body.get("query", "")
        variables = body.get("variables") or None
        operation_name = body.get("operationName")
        # Introspection queries are sync-safe; everything else too.
        result = graphql_sync(
            schema,
            query,
            variable_values=variables,
            operation_name=operation_name,
            context_value={
                "http_headers": {
                    k.lower(): v for k, v in request.headers.items()
                },
            },
        )
        payload = {}
        if result.errors:
            payload["errors"] = [{"message": str(e)} for e in result.errors]
        payload["data"] = result.data
        return JSONResponse(payload)

    async def graphql_auth_endpoint(request: Request):
        # An upstream that refuses everything, introspection included,
        # without an Authorization header — like GitHub or an API gateway.
        if not request.headers.get("authorization"):
            return JSONResponse({"message": "Unauthorized"}, status_code=401)
        return await graphql_endpoint(request)

    return Starlette(routes=[
        Route("/graphql", graphql_endpoint, methods=["POST"]),
        Route("/graphql-auth", graphql_auth_endpoint, methods=["POST"]),
    ])


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server_in_thread(app, port) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for the server to be ready.
    deadline = time.time() + 10
    while time.time() < deadline:
        if server.started:
            return server, thread
        time.sleep(0.05)
    raise RuntimeError("test server failed to start")


@pytest.fixture(scope="session")
def upstream_graphql():
    """Session-scoped fake upstream GraphQL server on a local port."""
    port = _find_free_port()
    schema = _build_demo_schema()
    app = _make_upstream_app(schema)
    server, thread = _run_server_in_thread(app, port)
    url = f"http://127.0.0.1:{port}/graphql"
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def upstream_graphql_auth(upstream_graphql):
    """Same upstream, on a path that requires Authorization for everything."""
    return upstream_graphql.replace("/graphql", "/graphql-auth")
