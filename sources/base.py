"""The price-source contract (Adapter / Strategy pattern).

Every source — the mock store now, eBay and Best Buy in Phase 3, a scraper in
Phase 5 — implements this ONE interface and returns the SAME `NormalizedOffer`
shape. The pipeline and the database never learn which source ran; they just
handle normalized offers. That uniformity is the whole point: adding a source is
a new file, not a change to the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


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
