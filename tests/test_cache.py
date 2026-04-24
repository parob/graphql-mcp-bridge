"""Tests for the TTLCache + stampede-protection layer."""

import asyncio

import pytest

from bridge.cache import InstanceCache, normalize_upstream_url


def test_normalize_lowercases_host_and_strips_trailing_slash():
    assert normalize_upstream_url(
        "HTTPS://Example.COM/graphql/") == "https://example.com/graphql"
    assert normalize_upstream_url(
        "https://example.com:8080/graphql") == "https://example.com:8080/graphql"


@pytest.mark.asyncio
async def test_get_or_build_caches_single_value():
    cache: InstanceCache[str] = InstanceCache(maxsize=10, ttl_seconds=60)
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return f"value-{calls}"

    v1 = await cache.get_or_build("k1", build)
    v2 = await cache.get_or_build("k1", build)
    assert v1 == v2 == "value-1"
    assert calls == 1


@pytest.mark.asyncio
async def test_concurrent_misses_coalesce():
    cache: InstanceCache[str] = InstanceCache(maxsize=10, ttl_seconds=60)
    calls = 0
    release = asyncio.Event()

    async def build():
        nonlocal calls
        calls += 1
        await release.wait()
        return f"value-{calls}"

    # Kick off 20 concurrent get_or_build for the same key.
    tasks = [asyncio.create_task(cache.get_or_build("k1", build)) for _ in range(20)]
    await asyncio.sleep(0.01)
    release.set()
    results = await asyncio.gather(*tasks)
    assert all(r == "value-1" for r in results)
    assert calls == 1


@pytest.mark.asyncio
async def test_different_keys_build_independently():
    cache: InstanceCache[str] = InstanceCache(maxsize=10, ttl_seconds=60)

    async def build_a():
        return "a"

    async def build_b():
        return "b"

    a, b = await asyncio.gather(
        cache.get_or_build("a", build_a),
        cache.get_or_build("b", build_b),
    )
    assert a == "a"
    assert b == "b"


@pytest.mark.asyncio
async def test_invalidate_forces_rebuild():
    cache: InstanceCache[str] = InstanceCache(maxsize=10, ttl_seconds=60)
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    await cache.get_or_build("k1", build)
    assert calls == 1

    assert cache.invalidate("k1") is True
    await cache.get_or_build("k1", build)
    assert calls == 2

    assert cache.invalidate("k1") is True
    assert cache.invalidate("k1") is False
