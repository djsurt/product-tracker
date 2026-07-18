"""The 429 backpressure path.

A rate-limited source (HTTP 429) must surface as RateLimitedError so the worker
can treat it as backpressure instead of firing its normal retry storm — the very
behaviour that keeps a free-tier key permanently throttled.
"""

import httpx
import pytest

from sources.base import (
    ListingGoneError,
    RateLimitedError,
    raise_if_listing_gone,
    raise_if_rate_limited,
)


def _resp(status_code: int) -> httpx.Response:
    request = httpx.Request("GET", "https://api.example.test/item/1")
    return httpx.Response(status_code, request=request)


def test_raise_if_rate_limited_raises_on_429():
    with pytest.raises(RateLimitedError):
        raise_if_rate_limited(_resp(429))


@pytest.mark.parametrize("status", [200, 404, 410, 500, 503])
def test_raise_if_rate_limited_passes_other_statuses(status):
    # Only 429 is backpressure; every other status keeps its normal meaning.
    raise_if_rate_limited(_resp(status))


def test_429_is_not_swallowed_as_listing_gone():
    # A 429 must not be mistaken for a gone listing (which would wrongly delist a
    # perfectly healthy offer). raise_if_listing_gone leaves 429 alone.
    raise_if_listing_gone(_resp(429))
    with pytest.raises(RateLimitedError):
        raise_if_rate_limited(_resp(429))


def test_listing_gone_still_wins_on_404():
    with pytest.raises(ListingGoneError):
        raise_if_listing_gone(_resp(404))
