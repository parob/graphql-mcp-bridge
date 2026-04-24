"""Tests for the in-memory token-bucket rate limiter."""

import pytest

from bridge.rate_limit import TokenBucketLimiter, parse_rate


def test_parse_rate_units():
    assert parse_rate("60/minute") == (60, 1.0)
    assert parse_rate("10/second") == (10, 10.0)
    assert parse_rate("3600/hour") == (3600, 1.0)


def test_parse_rate_rejects_bad_unit():
    with pytest.raises(ValueError):
        parse_rate("10/fortnight")


@pytest.mark.asyncio
async def test_bucket_enforces_capacity():
    limiter = TokenBucketLimiter("3/minute")
    # 3 allowed, 4th denied.
    assert await limiter.acquire("ip-a") is True
    assert await limiter.acquire("ip-a") is True
    assert await limiter.acquire("ip-a") is True
    assert await limiter.acquire("ip-a") is False


@pytest.mark.asyncio
async def test_buckets_are_isolated_per_key():
    limiter = TokenBucketLimiter("1/minute")
    assert await limiter.acquire("ip-a") is True
    assert await limiter.acquire("ip-a") is False
    assert await limiter.acquire("ip-b") is True
