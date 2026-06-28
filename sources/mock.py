"""MockStoreSource: the first adapter.

It talks HTTP to our own mock storefront (mock_store/). Building the whole
pipeline against a source we control means zero anti-bot/captcha pain — we can
focus on queues, scheduling, and data flow. Phase 3 swaps in real APIs behind
the exact same interface.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from core.settings import get_settings
from sources.base import NormalizedOffer


class MockStoreSource:
    name = "mock"

    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = (base_url or get_settings().mock_store_url).rstrip("/")
        self.timeout = timeout

    def search(self, query: str) -> list[NormalizedOffer]:
        data = self._get("/search", params={"q": query})
        return [self._to_offer(item) for item in data["results"]]

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        data = self._get(f"/products/{source_product_id}")
        return self._to_offer(data)

    # --- internals ---
    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = httpx.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _to_offer(self, item: dict) -> NormalizedOffer:
        # str(...) around the price keeps Decimal exact (don't let a float in).
        return NormalizedOffer(
            source=self.name,
            source_product_id=item["source_product_id"],
            title=item["title"],
            price=Decimal(str(item["price"])),
            currency=item["currency"],
            url=item["url"],
            available=item["available"],
        )
