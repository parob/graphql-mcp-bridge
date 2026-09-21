"""Build-or-fetch a GraphQLMCP instance + live sub-app for a given upstream URL."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Mapping, Sequence

from graphql_mcp.server import build_remote_mcp, GraphQLMCP

from bridge.cache import InstanceCache, instance_cache_key
from bridge.config import Settings

logger = logging.getLogger(__name__)

# How long to wait for a retired instance's session manager to wind down.
CLOSE_TIMEOUT_SECONDS = 10


@dataclass
class BuiltInstance:
    """A GraphQLMCP plus its running streamable-http ASGI sub-app.

    The sub-app's lifespan runs in its own task (``_lifespan_task``) for as
    long as the instance is cached, so FastMCP's session manager is ready
    without per-request startup cost. Running it in a dedicated task matters:
    anyio cancel scopes must be exited by the task that entered them, and
    the request that triggered the build is long gone by the time the
    instance is retired.
    """

    graphql_mcp: GraphQLMCP
    sub_app: object
    _stop: asyncio.Event
    _lifespan_task: asyncio.Task

    async def close(self) -> None:
        """Exit the sub-app lifespan and wait for it to finish."""
        self._stop.set()
        try:
            await asyncio.wait_for(self._lifespan_task, CLOSE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("bridge: instance lifespan did not exit in time")
        except Exception as e:  # noqa: BLE001 — shutdown must not raise
            logger.warning("bridge: instance lifespan exited with error: %s", e)


class Proxy:
    """Owns the cache of live MCP sub-apps and constructs new ones on miss."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.forward_headers = _normalize_forward_headers(settings.forward_headers)
        self.cache: InstanceCache[BuiltInstance] = InstanceCache(
            maxsize=settings.cache_maxsize,
            ttl_seconds=settings.cache_ttl_seconds,
            on_evict=lambda built: built.close(),
        )

    def cache_key(self, upstream_url: str, forwarded: Mapping[str, str] | None) -> str:
        return instance_cache_key(upstream_url, forwarded)

    async def get(
        self, upstream_url: str, forwarded: Mapping[str, str] | None = None,
    ) -> BuiltInstance:
        """The instance for ``upstream_url`` as seen with the caller's
        ``forwarded`` headers (already filtered through the allowlist)."""
        key = self.cache_key(upstream_url, forwarded)
        return await self.cache.get_or_build(
            key, lambda: self._build(upstream_url, forwarded))

    async def close_all(self) -> None:
        await self.cache.close_all()

    async def _build(
        self, upstream_url: str, forwarded: Mapping[str, str] | None,
    ) -> BuiltInstance:
        logger.info("bridge: building MCP instance for %s", upstream_url)
        # build_remote_mcp is synchronous — run in a worker so we don't
        # block the event loop while the upstream responds to introspection.
        # The caller's forwarded headers unlock introspection on upstreams
        # that require auth; they are used for that one request only and
        # never stored on the instance (each tool call forwards its own).
        instance = await asyncio.to_thread(
            build_remote_mcp,
            upstream_url,
            headers={"User-Agent": self.settings.user_agent},
            introspection_headers=dict(forwarded) if forwarded else None,
            timeout=self.settings.upstream_timeout_seconds,
            graphql_http=True,
            allow_mutations=True,
            forward_headers=self.forward_headers,
        )
        # Build the sub-app with both the MCP endpoint (at "/mcp") and the
        # GraphiQL explorer + GraphQL proxy (graphql_http=True, served at any
        # non-"/mcp" path). The outer Bridge route normalizes the incoming
        # path to "/mcp" for MCP traffic or "/graphql" for the explorer before
        # dispatching here. graphql-mcp's remote_client (set by
        # build_remote_mcp) lets the GraphQL proxy forward queries upstream.
        sub_app = instance.http_app(
            transport="streamable-http",
            stateless_http=True,
            path="/mcp",
            graphql_http=True,
        )
        return BuiltInstance(
            graphql_mcp=instance,
            sub_app=sub_app,
            **await _start_lifespan(sub_app),
        )


async def _start_lifespan(sub_app) -> dict:
    """Enter ``sub_app``'s lifespan in a dedicated task and wait until it is
    up. Returns the stop event and task for ``BuiltInstance``."""
    stop = asyncio.Event()
    ready: asyncio.Future = asyncio.get_running_loop().create_future()

    async def run() -> None:
        try:
            async with sub_app.router.lifespan_context(sub_app):
                ready.set_result(None)
                await stop.wait()
        except BaseException as e:  # noqa: BLE001 — report, never crash the loop
            if not ready.done():
                ready.set_exception(e)
            else:
                raise

    task = asyncio.create_task(run())
    await ready
    return {"_stop": stop, "_lifespan_task": task}


def _normalize_forward_headers(val: Sequence[str] | str):
    if isinstance(val, str):
        if val.strip() == "*":
            return "*"
        parts = [p.strip() for p in val.split(",") if p.strip()]
        return parts or None
    parts = [p for p in val if p]
    return list(parts) or None
