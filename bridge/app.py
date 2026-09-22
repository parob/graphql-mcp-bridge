"""Bridge Starlette app — catch-all MCP route that proxies any GraphQL endpoint."""

from __future__ import annotations

import contextlib
import html
import json
import logging
import sys
import time
from typing import Awaitable, Callable
from urllib.parse import unquote, urlparse

from graphql_mcp.server import select_forward_headers
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from bridge.cache import normalize_upstream_url
from bridge.config import Settings, load_settings
from bridge.encoding import decode_upstream
from bridge.landing import landing_html
from bridge.proxy import Proxy
from bridge.rate_limit import TokenBucketLimiter
from bridge.ssrf import UpstreamValidationError, validate_upstream_url

logger = logging.getLogger(__name__)
# One JSON line per proxied request (see _access_log). Its own logger and
# handler so the line reaches stderr verbatim: Cloud Logging then parses it
# into a structured entry instead of a text payload.
access_logger = logging.getLogger("bridge.access")


def configure_logging() -> None:
    """Send the bridge's own INFO logs to stderr.

    Python's root logger only prints WARNING and above until someone
    configures it, which silently dropped every access-log and
    "building instance" line in production.
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO, stream=sys.stderr,
            format="%(levelname)s:%(name)s:%(message)s")
    if not access_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        access_logger.addHandler(handler)
        access_logger.propagate = False
    access_logger.setLevel(logging.INFO)
    # The MCP SDK logs two INFO lines per request ("Processing request",
    # "Terminating session"); the access line above already covers that.
    logging.getLogger("mcp").setLevel(logging.WARNING)


# Human-facing docs for the Bridge, linked from the landing page and the JSON
# service-info response. This is the canonical GitHub Pages docs domain —
# graphql-mcp.com is only a frameset wrapper and can't serve sub-paths.
DOCS_URL = "https://graphql-mcp.parob.com/bridge"
REPO_URL = "https://github.com/parob/graphql-mcp-bridge"

# The landing page is static (the MCP base is derived client-side), so render
# it once at import rather than per request.
LANDING_HTML = landing_html(DOCS_URL, REPO_URL)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _wants_html(request: Request) -> bool:
    """A browser visiting the bare MCP URL (GET asking for HTML)."""
    return request.method == "GET" and "text/html" in request.headers.get(
        "accept", "")


def _request_origin(request: Request) -> str:
    """The absolute ``scheme://host`` a visitor reached us on, honoring the
    proxy headers Cloud Run / the load balancer set in front of us."""
    proto = (request.headers.get("x-forwarded-proto", "").split(",")[0]
             .strip() or request.url.scheme)
    netloc = (request.headers.get("x-forwarded-host")
              or request.headers.get("host") or request.url.netloc)
    return f"{proto}://{netloc}" if netloc else ""


def _is_plain_html(headers: list) -> bool:
    """True for an uncompressed HTML response — the only shape we can safely
    rewrite. Anything encoded (gzip/br) or non-HTML streams through untouched.
    """
    ctype = enc = b""
    for key, value in headers:
        lowered = key.lower()
        if lowered == b"content-type":
            ctype = value
        elif lowered == b"content-encoding":
            enc = value
    return b"text/html" in ctype.lower() and not enc


def _mcp_url_script(mcp_url: str) -> bytes:
    """A <script> declaring the canonical MCP endpoint for the GraphiQL page.

    graphql-mcp's MCP plugin (>= 2.1.4) reads ``window.__GRAPHQL_MCP_URL__``
    and, when set, targets it instead of guessing "<current path>/mcp" — which
    is wrong here, because we serve the explorer one level below the endpoint.

    ``mcp_url`` is built from request headers, so the JSON string is additionally
    escaped for the ``<script>`` context (a literal ``<`` could otherwise end
    the element early).
    """
    literal = (json.dumps(mcp_url).replace("<", "\\u003c")
               .replace(">", "\\u003e").replace("&", "\\u0026"))
    return f"<script>window.__GRAPHQL_MCP_URL__={literal};</script>".encode()


def _explorer_page(target: str, mcp_url: str, host: str) -> HTMLResponse:
    """A page shown to a human who opened a bare MCP URL in a browser. MCP
    endpoints only speak JSON-RPC over POST, so instead of a confusing error we
    explain what this is and offer a button to the GraphiQL explorer. No
    auto-redirect — the visitor clicks through when they're ready.

    ``target``/``mcp_url``/``host`` are derived from the user-controlled path,
    so every interpolation is HTML-escaped.
    """
    t = html.escape(target, quote=True)
    mcp = html.escape(mcp_url, quote=True)
    h = html.escape(host or "this GraphQL API")
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP endpoint — {h}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    font: 16px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: #0d1117; color: #e6edf3;
  }}
  .card {{
    max-width: 34rem; padding: 2.5rem; margin: 1rem;
    background: #161b22; border: 1px solid #30363d; border-radius: 14px;
  }}
  h1 {{ margin: 0 0 .75rem; font-size: 1.4rem; }}
  p {{ margin: .6rem 0; color: #aab2bd; }}
  strong {{ color: #e6edf3; }}
  .go {{
    display: inline-block; margin-top: 1.25rem; padding: .6rem 1.1rem;
    background: #238636; color: #fff; text-decoration: none; border-radius: 8px;
    font-weight: 600;
  }}
  .go:hover {{ background: #2ea043; }}
  .mcp {{
    margin-top: 1.5rem; padding: .75rem; font-size: .85rem;
    background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
    word-break: break-all;
  }}
  .mcp span {{ color: #7d8590; display: block; margin-bottom: .25rem; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
</style>
</head>
<body>
  <main class="card">
    <h1>This is an MCP endpoint</h1>
    <p>You've opened a live <strong>MCP (Model Context Protocol)</strong>
       endpoint for <strong>{h}</strong>. MCP endpoints speak JSON-RPC and are
       meant for AI agents, not browsers.</p>
    <p>To browse the schema and run queries in your browser, open the
       <strong>GraphiQL explorer</strong>:</p>
    <a class="go" href="{t}">Open the GraphQL explorer →</a>
    <div class="mcp">
      <span>MCP endpoint (paste this into your AI client):</span>
      <code>{mcp}</code>
    </div>
  </main>
</body>
</html>"""
    return HTMLResponse(body)


def _access_log(
    request: Request, upstream: str, kind: str, status: int,
    started: float, cache_hit: bool, forwarded: dict[str, str],
) -> None:
    """Emit one structured line per proxied request so usage is queryable
    (e.g. in Cloud Logging): which upstreams, how often, latency, hit/miss.

    ``credentials`` lists the names of the forwarded headers, never their
    values, so an upstream 401 shows whether the caller sent anything.
    """
    access_logger.info(json.dumps({
        "severity": "INFO",
        "message": "bridge_request",
        "event": "bridge_request",
        "upstream": urlparse(upstream).hostname or upstream,
        "kind": kind,
        "method": request.method,
        "status": status,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "cache": "hit" if cache_hit else "miss",
        "credentials": sorted(forwarded),
        "client_ip": _client_ip(request),
    }))


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or load_settings()
    configure_logging()
    proxy = Proxy(settings)
    limiter = TokenBucketLimiter(settings.rate_limit)

    async def root(request: Request) -> Response:
        # A browser landing on the bare host gets the explainer + URL mapper;
        # API/programmatic clients (no text/html) keep the JSON service-info.
        if _wants_html(request):
            return HTMLResponse(LANDING_HTML)
        return JSONResponse({
            "service": "graphql-mcp-bridge",
            "docs": DOCS_URL,
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

        # Browser hitting the bare MCP URL → explain what it is and offer a
        # button to the GraphiQL explorer (the raw MCP endpoint only speaks
        # JSON-RPC over POST). "rest" is empty for the bare URL, "graphql" for
        # the explorer, and "<...>/mcp" for the MCP endpoint the GraphiQL plugin
        # derives.
        if rest == "" and _wants_html(request):
            bare = raw_path.rstrip("/")
            origin = _request_origin(request)
            try:
                upstream_host = urlparse(
                    decode_upstream(upstream_raw)).hostname or ""
            except Exception:
                upstream_host = ""
            return await _explorer_page(
                target=bare + "/graphql",
                mcp_url=origin + bare,
                host=upstream_host,
            )(scope, receive, send)

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

        # The caller's allowlisted headers (Authorization, X-API-Key, ...)
        # also unlock schema introspection on upstreams that require auth,
        # and select the cache entry built with those credentials.
        forwarded = select_forward_headers(
            request.headers, proxy.forward_headers)
        cache_hit = proxy.cache.peek(
            proxy.cache_key(upstream, forwarded)) is not None
        try:
            built = await proxy.get(upstream, forwarded)
        except Exception as e:
            logger.warning(
                "bridge: failed to build instance for %s: %s", upstream, e)
            _access_log(
                request, upstream, kind, 502, started, cache_hit, forwarded)
            return await JSONResponse(
                {"error": f"failed to introspect upstream: {e}"},
                status_code=502)(scope, receive, send)

        # Rewrite the scope so the sub-app sees a clean, normalized path.
        sub_scope = dict(scope)
        sub_scope["path"] = internal_path
        sub_scope["raw_path"] = internal_path.encode()
        sub_scope["root_path"] = scope.get(
            "root_path", "") + scope.get("path", "")

        # The explorer lives at /mcp/<token>/graphql but the MCP endpoint it
        # should talk to is /mcp/<token>, so tell the plugin outright rather
        # than letting it derive the wrong path from the URL it was served on.
        inject = None
        if kind == "graphql":
            inject = _mcp_url_script(
                _request_origin(request) + "/mcp/" + token)

        # Wrap send to record the response status for the access log and, for
        # the explorer HTML only, splice the injection in before </head>.
        st = {"status": 0, "buffer": False, "headers": [], "body": bytearray()}

        async def _send(message) -> None:
            if message["type"] == "http.response.start":
                st["status"] = message["status"]
                if inject and _is_plain_html(message.get("headers", [])):
                    # Hold the headers back: content-length changes once the
                    # body has grown by the injected script.
                    st["buffer"] = True
                    st["headers"] = list(message.get("headers", []))
                    return
                await send(message)
                return

            if st["buffer"] and message["type"] == "http.response.body":
                st["body"] += message.get("body", b"")
                if message.get("more_body"):
                    return
                body = bytes(st["body"])
                if b"</head>" in body:
                    body = body.replace(b"</head>", inject + b"</head>", 1)
                else:
                    body = inject + body
                headers = [(k, v) for (k, v) in st["headers"]
                           if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send({"type": "http.response.start",
                            "status": st["status"], "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

            await send(message)

        await built.sub_app(sub_scope, receive, _send)
        _access_log(
            request, upstream, kind, st["status"], started, cache_hit,
            forwarded)

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
        removed = await proxy.cache.invalidate_upstream(
            normalize_upstream_url(url))
        return JSONResponse({"invalidated": removed > 0, "entries": removed})

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        try:
            yield
        finally:
            # Exit every cached instance's session manager from a task that
            # is still alive, instead of leaving the async generators to be
            # finalized by the interpreter (which logged cross-task cancel
            # scope errors on every SIGTERM).
            await proxy.close_all()

    app = Starlette(
        lifespan=lifespan,
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
