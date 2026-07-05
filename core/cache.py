"""Redis-backed primitives for Phase 3: caching, rate limiting, idempotency.

All three are just different uses of the same Redis instance we already run as
the Celery broker. Each helper takes an optional `client` so tests can inject a
fake Redis instead of needing a live server.

- **Cache-aside** (best-deal cache): read cache -> on miss, compute + store.
- **Rate limiting** (fixed-window counter): cap calls/sec per source.
- **Idempotency lock** (SET NX EX): one worker owns an offer's fetch at a time.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

import redis

from core.settings import get_settings

settings = get_settings()

# decode_responses=True -> we get/put str, not bytes (simpler JSON handling).
_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> redis.Redis:
    return _redis


# --------------------------------------------------------------------------
# Cache-aside: the computed "best deal" for a product.
# The worker invalidates this whenever it records a new price, so reads are
# fast but never stale for long.
# --------------------------------------------------------------------------
def _best_deal_key(tracked_product_id: str) -> str:
    return f"bestdeal:{tracked_product_id}"


def get_cached_best_deal(
    tracked_product_id: str, client: redis.Redis | None = None
) -> dict | None:
    raw = (client or _redis).get(_best_deal_key(tracked_product_id))
    return json.loads(raw) if raw else None


def cache_best_deal(
    tracked_product_id: str,
    data: dict,
    ttl: int | None = None,
    client: redis.Redis | None = None,
) -> None:
    (client or _redis).set(
        _best_deal_key(tracked_product_id),
        json.dumps(data, default=str),  # default=str handles Decimal/datetime
        ex=ttl or settings.best_deal_cache_ttl_seconds,
    )


def invalidate_best_deal(
    tracked_product_id: str, client: redis.Redis | None = None
) -> None:
    (client or _redis).delete(_best_deal_key(tracked_product_id))


# --------------------------------------------------------------------------
# Rate limiting: fixed-window counter, one window (key) per source per second.
# Simple and atomic (INCR). The known limitation is burstiness at window
# boundaries; a token bucket / sliding window smooths that out (a Phase 5
# upgrade). Good enough to demonstrate "don't hammer a source".
# --------------------------------------------------------------------------
class RateLimiter:
    def __init__(
        self,
        client: redis.Redis | None = None,
        limit: int | None = None,
        window_seconds: int = 1,
    ) -> None:
        self.client = client or _redis
        self.limit = limit or settings.source_rate_limit_per_sec
        self.window = window_seconds

    def allow(self, source: str) -> bool:
        """Return True if a call to `source` is within budget right now."""
        window_id = int(time.time()) // self.window
        key = f"ratelimit:{source}:{window_id}"
        count = self.client.incr(key)
        if count == 1:
            # First hit in this window: set TTL so the key self-cleans.
            self.client.expire(key, self.window + 1)
        return count <= self.limit


# --------------------------------------------------------------------------
# Idempotency lock: SET key NX EX. NX = only set if absent (atomic), EX = auto
# expire so a crashed worker can't hold the lock forever.
# --------------------------------------------------------------------------
@contextmanager
def offer_lock(
    offer_id: str, ttl: int | None = None, client: redis.Redis | None = None
) -> Iterator[bool]:
    """Yield True if we acquired the lock for this offer, False otherwise.

    Caller checks the value: if False, another worker is already fetching this
    offer, so we skip rather than do duplicate work.
    """
    r = client or _redis
    key = f"lock:offer:{offer_id}"
    acquired = bool(r.set(key, "1", nx=True, ex=ttl or settings.fetch_lock_ttl_seconds))
    try:
        yield acquired
    finally:
        if acquired:
            r.delete(key)


def best_deal_payload(**kwargs: Any) -> dict:
    """Tiny helper so callers build the cached dict consistently."""
    return dict(kwargs)


# --- 3D generation quota (Phase 10) ----------------------------------------
def _model3d_month_key() -> str:
    from datetime import datetime, timezone

    return f"model3d:count:{datetime.now(timezone.utc):%Y-%m}"


def model3d_quota_ok(client: redis.Redis | None = None, cap: int | None = None) -> bool:
    """True if another 3D generation is allowed this calendar month."""
    r = client or get_redis()
    limit = cap if cap is not None else get_settings().model3d_monthly_cap
    used = int(r.get(_model3d_month_key()) or 0)
    return used < limit


def model3d_quota_spend(client: redis.Redis | None = None) -> None:
    """Count one generation against this month's cap (60d expiry, self-cleaning)."""
    r = client or get_redis()
    key = _model3d_month_key()
    if r.incr(key) == 1:
        r.expire(key, 60 * 24 * 3600)
