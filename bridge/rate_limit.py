"""Minimal per-key token-bucket rate limiter.

A single process memory bucket is enough for Bridge's starting deployment
(single Cloud Run instance). If Bridge grows to multiple instances, swap this
for a Redis-backed limiter.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


def parse_rate(spec: str) -> tuple[float, float]:
    """Parse a '60/minute' style spec into (capacity, refill_per_second)."""
    spec = spec.strip().lower()
    count_str, _, unit = spec.partition("/")
    count = float(count_str)
    unit = unit.strip() or "second"
    per_second = {
        "second": 1.0,
        "minute": 60.0,
        "hour": 3600.0,
        "day": 86400.0,
    }.get(unit)
    if per_second is None:
        raise ValueError(f"unknown rate unit: {unit!r}")
    return count, count / per_second


class TokenBucketLimiter:
    """In-memory token-bucket limiter keyed by client identity."""

    def __init__(self, spec: str):
        self.capacity, self.refill_per_second = parse_rate(spec)
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, last_refill=now)
                self._buckets[key] = bucket
            elapsed = now - bucket.last_refill
            bucket.tokens = min(
                self.capacity,
                bucket.tokens + elapsed * self.refill_per_second,
            )
            bucket.last_refill = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False
