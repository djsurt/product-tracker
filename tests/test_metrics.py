"""Tests for the Redis-backed metrics counters (Phase 5), using fakeredis."""

import fakeredis

from core import metrics


def _fake():
    return fakeredis.FakeRedis(decode_responses=True)


def test_inc_and_render():
    r = _fake()
    metrics.inc("deal_fetch_success_total", client=r)
    metrics.inc("deal_fetch_success_total", 2, client=r)

    out = metrics.render(client=r)
    assert "# TYPE deal_fetch_success_total counter" in out
    assert "deal_fetch_success_total 3" in out


def test_unset_counter_renders_zero():
    out = metrics.render(client=_fake())
    # Every registered counter appears, defaulting to 0.
    assert "deal_dead_letter_total 0" in out
    for name in metrics.COUNTERS:
        assert f"{name} " in out


def test_inc_never_raises_on_redis_error():
    class Boom:
        def incrby(self, *a, **k):
            raise RuntimeError("redis down")

    # Metrics are best-effort: a broken backend must not break the caller.
    metrics.inc("deal_fetch_success_total", client=Boom())  # no exception
