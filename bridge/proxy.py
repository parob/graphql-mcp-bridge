"""Build-or-fetch a GraphQLMCP instance + live sub-app for a given upstream URL."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Sequence

from graphql_mcp.server import build_remote_mcp, GraphQLMCP

from bridge.cache import InstanceCache, normalize_upstream_url
from bridge.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class BuiltInstance:
    """A GraphQLMCP plus its running streamable-http ASGI sub-app.

    The sub-app's lifespan is entered at build time so that FastMCP's
    session manager is initialized and ready to accept requests without
    any per-request startup cost. The `exit_stack` holds the contexts
    that must be exited when the instance is retired.
    """

    graphql_mcp: GraphQLMCP
    sub_app: object
    exit_stack: AsyncExitStack


class Proxy:
    """Owns the cache of live MCP sub-apps and constructs new ones on miss."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache: InstanceCache[BuiltInstance] = InstanceCache(
            maxsize=settings.cache_maxsize,
            ttl_seconds=settings.cache_ttl_seconds,
        )

    async def get(self, upstream_url: str) -> BuiltInstance:
        key = normalize_upstream_url(upstream_url)
        return await self.cache.get_or_build(
            key, lambda: self._build(upstream_url))

    async def _build(self, upstream_url: str) -> BuiltInstance:
        logger.info("bridge: building MCP instance for %s", upstream_url)
        # build_remote_mcp is synchronous — run in a worker so we don't
        # block the event loop while the upstream responds to introspection.
        instance = await asyncio.to_thread(
            build_remote_mcp,
            upstream_url,
            headers={"User-Agent": self.settings.user_agent},
            timeout=self.settings.upstream_timeout_seconds,
            graphql_http=True,
            allow_mutations=True,
            forward_headers=_normalize_forward_headers(
                self.settings.forward_headers),
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
        exit_stack = AsyncExitStack()
        lifespan = sub_app.router.lifespan_context
        await exit_stack.enter_async_context(lifespan(sub_app))
        return BuiltInstance(
            graphql_mcp=instance, sub_app=sub_app, exit_stack=exit_stack)


def _normalize_forward_headers(val: Sequence[str] | str):
    if isinstance(val, str):
        if val.strip() == "*":
            return "*"
        parts = [p.strip() for p in val.split(",") if p.strip()]
        return parts or None
    parts = [p for p in val if p]
    return list(parts) or None
