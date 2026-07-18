"""The price-source contract (Adapter / Strategy pattern).

Every source — the mock store now, eBay and Best Buy in Phase 3, a scraper in
Phase 5 — implements this ONE interface and returns the SAME `NormalizedOffer`
shape. The pipeline and the database never learn which source ran; they just
handle normalized offers. That uniformity is the whole point: adding a source is
a new file, not a change to the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable

# Pull the first number (optional thousands separators / decimals) out of a
# messy price string like "$1,199.00" or "USD 49.99". Shared by every adapter
# that parses display prices, so a parsing fix lands in one place.
_PRICE_RE = re.compile(r"[\d,]+\.?\d*")


def parse_price(raw: object) -> Decimal | None:
    """Extract a Decimal from a money string, or None if there's no usable value.

    Defensive on purpose: a regex match isn't a guarantee of a parseable number
    (e.g. "," or "."), so the Decimal() conversion is guarded — a bad price means
    "skip this offer", never an exception that aborts a whole discovery batch.
    """
    if raw is None:
        return None
    match = _PRICE_RE.search(str(raw))
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


class ListingGoneError(Exception):
    """The listing no longer exists at the source (ended/removed — HTTP 404/410
    on a fetch). Permanent, unlike a transient fetch failure: the worker must
    delist the offer and stop refreshing it, never retry/dead-letter it.
    """


class RateLimitedError(Exception):
    """The source is refusing us for now because we're over its rate/quota
    budget (HTTP 429). Unlike a normal fetch failure this is NOT retryable in the
    usual sense: retrying immediately just deepens the hole and keeps us throttled
    (the classic self-inflicted 429 storm). The worker treats it as backpressure
    — skip this cycle, no exponential-retry burst, no dead-letter — and lets the
    next scheduled sweep try again once budget has recovered.
    """


def raise_if_listing_gone(resp) -> None:
    """Translate an HTTP "this item no longer exists" into ListingGoneError.

    Call before raise_for_status() in any adapter's fetch(): 404/410 on an
    item-by-id read means the listing is gone for good; every other error status
    keeps its normal (retryable) meaning.
    """
    if resp.status_code in (404, 410):
        raise ListingGoneError(f"{resp.request.url}: HTTP {resp.status_code}")


def raise_if_rate_limited(resp) -> None:
    """Translate an HTTP 429 into RateLimitedError.

    Call before raise_for_status() in any adapter's search()/fetch(): a 429 means
    "you're over budget, back off" — hammering it with immediate retries is what
    keeps a free-tier key permanently throttled. Surfacing a distinct exception
    lets the worker apply backpressure instead of its normal retry storm.
    """
    if resp.status_code == 429:
        raise RateLimitedError(f"{resp.request.url}: HTTP 429 Too Many Requests")


@dataclass(frozen=True)
class NormalizedOffer:
    """A source's answer, translated into our internal vocabulary."""

    source: str               # which adapter produced this, e.g. "mock"
    source_product_id: str    # the id this product has *on that source*
    title: str
    price: Decimal
    currency: str
    url: str                  # where a buyer would go
    available: bool
    # Optional product image. Defaulted so existing adapters keep working.
    image_url: str | None = None


@runtime_checkable
class PriceSource(Protocol):
    """Implement this to add a new source. `name` must be stable and unique —
    it's stored on each Offer row so we can route refreshes back to the right
    adapter.
    """

    name: str

    def search(self, query: str) -> list[NormalizedOffer]:
        """Find candidate offers for a wishlist query (discovery)."""
        ...

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        """Re-read the current price of one known product (refresh)."""
        ...
