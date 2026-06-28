"""Tests for the Redis primitives, using fakeredis (no live server needed)."""

import fakeredis

from core.cache import (
    RateLimiter,
    cache_best_deal,
    get_cached_best_deal,
    invalidate_best_deal,
    offer_lock,
)


def _fake():
    return fakeredis.FakeRedis(decode_responses=True)


def test_rate_limiter_allows_then_blocks():
    r = _fake()
    rl = RateLimiter(client=r, limit=3, window_seconds=60)  # one window for test
    assert [rl.allow("ebay") for _ in range(5)] == [True, True, True, False, False]
    # each source has its own independent budget
    assert rl.allow("bestbuy") is True


def test_offer_lock_is_mutually_exclusive():
    r = _fake()
    with offer_lock("offer-1", ttl=10, client=r) as first:
        assert first is True
        with offer_lock("offer-1", ttl=10, client=r) as second:
            assert second is False  # already held
    # lock released after the first context exits
    with offer_lock("offer-1", ttl=10, client=r) as again:
        assert again is True


def test_best_deal_cache_roundtrip_and_invalidate():
    r = _fake()
    assert get_cached_best_deal("tp-1", client=r) is None

    cache_best_deal("tp-1", {"verdict": "great", "best_price": "10.00"}, client=r)
    got = get_cached_best_deal("tp-1", client=r)
    assert got["verdict"] == "great"

    invalidate_best_deal("tp-1", client=r)
    assert get_cached_best_deal("tp-1", client=r) is None
