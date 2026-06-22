"""TTL cache of built GraphQLMCP instances, with per-key stampede protection.

The cache is keyed by a normalized upstream URL. On a miss, one coroutine
does the expensive introspection + tool build; every other coroutine waiting
on the same key awaits the single in-flight Future, so an N-way burst for a
cold upstream triggers exactly one introspection.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Generic, TypeVar
from urllib.parse import urlparse

from cachetools import TTLCache

T = TypeVar("T")


def normalize_upstream_url(raw: str) -> str:
    """Canonical form used as a cache key — stable across trivial variants."""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{host}{port}{path}{query}"


class InstanceCache(Generic[T]):
    """Async-safe TTL cache with per-key coalesced builds."""

    def __init__(self, maxsize: int, ttl_seconds: int):
        self._cache: TTLCache[str, T] = TTLCache(
            maxsize=maxsize, ttl=ttl_seconds)
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def get_or_build(
        self,
        key: str,
        builder: Callable[[], Awaitable[T]],
    ) -> T:
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        lock = await self._lock_for(key)
        async with lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            instance = await builder()
            self._cache[key] = instance
            return instance

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def invalidate(self, key: str) -> bool:
        return self._cache.pop(key, None) is not None

    def peek(self, key: str) -> T | None:
        """Return the cached value without building — for hit/miss logging."""
        return self._cache.get(key)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
