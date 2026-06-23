"""Bridge Starlette app — catch-all MCP route that proxies any GraphQL endpoint."""

from __future__ import annotations

import json
import logging
import time
from typing import Awaitable, Callable
from urllib.parse import unquote, urlparse

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from bridge.cache import normalize_upstream_url
from bridge.config import Settings, load_settings
from bridge.encoding import decode_upstream
from bridge.proxy import Proxy
from bridge.rate_limit import TokenBucketLimiter
from bridge.ssrf import UpstreamValidationError, validate_upstream_url

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _wants_html(request: Request) -> bool:
    """A browser visiting the bare MCP URL (GET asking for HTML)."""
    return request.method == "GET" and "text/html" in request.headers.get(
        "accept", "")


def _access_log(
    request: Request, upstream: str, kind: str, status: int,
    started: float, cache_hit: bool,
) -> None:
    """Emit one structured line per proxied request so usage is queryable
    (e.g. in Cloud Logging): which upstreams, how often, latency, hit/miss."""
    logger.info(json.dumps({
        "event": "bridge_request",
        "upstream": urlparse(upstream).hostname or upstream,
        "kind": kind,
        "method": request.method,
        "status": status,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "cache": "hit" if cache_hit else "miss",
        "client_ip": _client_ip(request),
    }))


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or load_settings()
    proxy = Proxy(settings)
    limiter = TokenBucketLimiter(settings.rate_limit)

    async def root(_request: Request) -> Response:
        return JSONResponse({
            "service": "graphql-mcp-bridge",
            "docs": "https://graphql-mcp.com/bridge",
            "usage": "/mcp/<upstream GraphQL URL>",
            "explorer": "/mcp/<upstream GraphQL URL>/graphql",
            "upstream_encodings": ["percent-encoded", "base64url"],
        })

    async def health(_request: Request) -> Response:
        return PlainTextResponse("ok")

    async def mcp_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        # Raw ASGI handler so we can cleanly delegate to the per-upstream sub-app.
        request = Request(scope, receive)
        started = time.monotonic()

        if not await limiter.acquire(_client_ip(request)):
            return await PlainTextResponse(
                "rate limit exceeded", status_code=429)(scope, receive, send)

        # Split the upstream token from any explorer sub-path using the *raw*
        # (undecoded) path. Both supported encodings — base64url and
        # percent-encoding — escape the upstream URL's own slashes, so a
        # literal "/" in the raw path only ever separates the token from the
        # sub-path. Routing on the decoded path would be ambiguous because
        # "%2F" decodes back into slashes.
        raw_path = (scope.get("raw_path")
                    or scope.get("path", "").encode()).decode("latin-1")
        raw_path = raw_path.split("?", 1)[0]
        after = raw_path[len("/mcp/"):] if raw_path.startswith("/mcp/") else ""
        token, _, rest = after.partition("/")
        rest = rest.strip("/")
        if not token:
            return await JSONResponse(
                {"error": "missing upstream URL in path"},
                status_code=400)(scope, receive, send)
        upstream_raw = unquote(token)

        # Browser hitting the bare MCP URL → send it to the GraphiQL explorer
        # (the raw MCP endpoint only speaks JSON-RPC over POST). "rest" is
        # empty for the bare URL, "graphql" for the explorer, and "<...>/mcp"
        # for the MCP endpoint the GraphiQL plugin derives.
        if rest == "" and _wants_html(request):
            target = raw_path.rstrip("/") + "/graphql"
            return await RedirectResponse(
                target, status_code=307)(scope, receive, send)

        is_mcp = rest == "" or rest == "mcp" or rest.endswith("/mcp")
        kind = "mcp" if is_mcp else "graphql"
        internal_path = "/mcp" if is_mcp else "/graphql"

        upstream = decode_upstream(upstream_raw)
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

        cache_hit = proxy.cache.peek(normalize_upstream_url(upstream)) is not None
        try:
            built = await proxy.get(upstream)
        except Exception as e:
            logger.warning(
                "bridge: failed to build instance for %s: %s", upstream, e)
            _access_log(request, upstream, kind, 502, started, cache_hit)
            return await JSONResponse(
                {"error": f"failed to introspect upstream: {e}"},
                status_code=502)(scope, receive, send)

        # Rewrite the scope so the sub-app sees a clean, normalized path.
        sub_scope = dict(scope)
        sub_scope["path"] = internal_path
        sub_scope["raw_path"] = internal_path.encode()
        sub_scope["root_path"] = scope.get(
            "root_path", "") + scope.get("path", "")

        # Capture the response status for the access log.
        status_seen = {"status": 0}

        async def _logging_send(message) -> None:
            if message["type"] == "http.response.start":
                status_seen["status"] = message["status"]
            await send(message)

        await built.sub_app(sub_scope, receive, _logging_send)
        _access_log(
            request, upstream, kind, status_seen["status"], started, cache_hit)

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
