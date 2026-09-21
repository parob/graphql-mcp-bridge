"""TTL cache of built GraphQLMCP instances, with per-key stampede protection.

The cache is keyed by a normalized upstream URL plus a digest of the caller's
forwarded credentials (see ``instance_cache_key``). On a miss, one coroutine
does the expensive introspection + tool build; every other coroutine waiting
on the same key awaits the single in-flight Future, so an N-way burst for a
cold upstream triggers exactly one introspection.

Entries that fall out of the cache (TTL, capacity, explicit invalidation,
process shutdown) are handed to ``on_evict`` so their resources can be
released; ``cachetools`` itself gives no eviction hook.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Awaitable, Callable, Generic, Mapping, TypeVar
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


def instance_cache_key(upstream_url: str, headers: Mapping[str, str] | None) -> str:
    """Cache key for one upstream as seen by one set of credentials.

    The schema is introspected with the caller's forwarded headers, so two
    callers with different credentials may legitimately see different
    schemas — and neither should be served an instance built from the
    other's. The headers themselves are never stored; only a digest.
    """
    key = normalize_upstream_url(upstream_url)
    if headers:
        canonical = "\n".join(
            f"{name.lower()}={value}"
            for name, value in sorted(headers.items(), key=lambda kv: kv[0].lower())
        )
        key += "#" + hashlib.sha256(canonical.encode()).hexdigest()[:32]
    return key


class InstanceCache(Generic[T]):
    """Async-safe TTL cache with per-key coalesced builds and eviction hook."""

    def __init__(
        self,
        maxsize: int,
        ttl_seconds: int,
        on_evict: Callable[[T], Awaitable[None]] | None = None,
    ):
        self._cache: TTLCache[str, T] = TTLCache(
            maxsize=maxsize, ttl=ttl_seconds)
        # Everything we have built and not yet released. Diffed against the
        # TTLCache to discover what it silently dropped.
        self._live: dict[str, T] = {}
        self._on_evict = on_evict
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def get_or_build(
        self,
        key: str,
        builder: Callable[[], Awaitable[T]],
    ) -> T:
        await self._release_dropped()
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
            self._live[key] = instance
            await self._release_dropped()
            return instance

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def _release_dropped(self) -> None:
        """Release instances the TTLCache expired or pushed out."""
        self._cache.expire()
        dropped = [k for k in self._live if k not in self._cache]
        for key in dropped:
            await self._release(self._live.pop(key))

    async def _release(self, instance: T) -> None:
        if self._on_evict is not None:
            await self._on_evict(instance)

    async def invalidate(self, key: str) -> bool:
        removed = self._cache.pop(key, None) is not None
        instance = self._live.pop(key, None)
        if instance is not None:
            await self._release(instance)
        return removed

    async def invalidate_upstream(self, normalized_url: str) -> int:
        """Drop every entry for an upstream, whatever credentials built it."""
        keys = [k for k in self._live
                if k == normalized_url or k.startswith(normalized_url + "#")]
        for key in keys:
            await self.invalidate(key)
        return len(keys)

    def peek(self, key: str) -> T | None:
        """Return the cached value without building — for hit/miss logging."""
        return self._cache.get(key)

    async def close_all(self) -> None:
        """Release every instance (process shutdown)."""
        self._cache.clear()
        live = list(self._live.values())
        self._live.clear()
        for instance in live:
            await self._release(instance)

    def clear(self) -> None:
        self._cache.clear()
        self._live.clear()

    def __len__(self) -> int:
        return len(self._cache)
