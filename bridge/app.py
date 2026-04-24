"""Bridge Starlette app — catch-all MCP route that proxies any GraphQL endpoint."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable
from urllib.parse import unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from bridge.cache import normalize_upstream_url
from bridge.config import Settings, load_settings
from bridge.proxy import Proxy
from bridge.rate_limit import TokenBucketLimiter
from bridge.ssrf import UpstreamValidationError, validate_upstream_url

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or load_settings()
    proxy = Proxy(settings)
    limiter = TokenBucketLimiter(settings.rate_limit)

    async def root(_request: Request) -> Response:
        return JSONResponse({
            "service": "graphql-mcp-bridge",
            "docs": "https://graphql-mcp.com/bridge",
            "usage": "/mcp/<url-encoded GraphQL endpoint>",
        })

    async def health(_request: Request) -> Response:
        return PlainTextResponse("ok")

    async def mcp_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        # Raw ASGI handler so we can cleanly delegate to the per-upstream sub-app.
        request = Request(scope, receive)

        if not await limiter.acquire(_client_ip(request)):
            return await PlainTextResponse(
                "rate limit exceeded", status_code=429)(scope, receive, send)

        upstream_encoded = scope.get("path_params", {}).get("upstream", "")
        if not upstream_encoded:
            return await JSONResponse(
                {"error": "missing upstream URL in path"},
                status_code=400)(scope, receive, send)

        upstream = unquote(upstream_encoded)
        try:
            upstream = validate_upstream_url(
                upstream,
                max_length=settings.max_upstream_url_length,
                allow_internal_hosts=settings.allow_internal_hosts,
            )
        except UpstreamValidationError as e:
            return await JSONResponse(
                {"error": f"invalid upstream: {e}"},
                status_code=400)(scope, receive, send)

        try:
            built = await proxy.get(upstream)
        except Exception as e:
            logger.warning(
                "bridge: failed to build instance for %s: %s", upstream, e)
            return await JSONResponse(
                {"error": f"failed to introspect upstream: {e}"},
                status_code=502)(scope, receive, send)

        # Rewrite the scope so the sub-app (rooted at "/") sees a clean path.
        sub_scope = dict(scope)
        sub_scope["path"] = "/"
        sub_scope["raw_path"] = b"/"
        sub_scope["root_path"] = scope.get(
            "root_path", "") + scope.get("path", "")

        await built.sub_app(sub_scope, receive, send)

    async def invalidate(request: Request) -> Response:
        secret = settings.admin_secret
        if not secret:
            return JSONResponse(
                {"error": "admin endpoint disabled"}, status_code=404)
        if request.headers.get("x-bridge-admin-secret") != secret:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        data = await request.json()
        url = (data or {}).get("url")
        if not url:
            return JSONResponse({"error": "missing url"}, status_code=400)
        removed = proxy.cache.invalidate(normalize_upstream_url(url))
        return JSONResponse({"invalidated": removed})

    app = Starlette(
        routes=[
            Route("/", root, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route(
                "/mcp/{upstream:path}",
                _asgi_route(mcp_endpoint),
                methods=["GET", "POST", "DELETE"],
            ),
            Route("/admin/invalidate", invalidate, methods=["POST"]),
        ],
    )
    app.state.limiter = limiter
    app.state.proxy = proxy
    app.state.settings = settings
    return app


def _asgi_route(
    handler: Callable[[Scope, Receive, Send], Awaitable[None]],
) -> Callable[[Request], Response]:
    """Adapt a raw ASGI handler into something Starlette's Route can dispatch.

    Starlette's Route expects a callable that takes a Request and returns a
    Response. To bypass that and run our own ASGI under the captured path
    params, we wrap in a Response that re-enters the handler on call.
    """

    class _ASGIAdapter(Response):
        def __init__(self, request: Request):
            self.request = request

        async def __call__(self, scope, receive, send):
            # Inject Starlette's captured path_params back into the scope so
            # the handler can see {upstream}.
            scope = dict(scope)
            scope["path_params"] = self.request.path_params
            await handler(scope, receive, send)

    async def endpoint(request: Request) -> Response:
        return _ASGIAdapter(request)

    return endpoint


# Module-level app for `uvicorn bridge.app:app`
app = create_app()
