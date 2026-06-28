"""Best Buy Products API adapter.

Simpler than eBay: a single API key (passed as a query param), no OAuth. Search
uses Best Buy's path-embedded query syntax `products((search=...))`; refresh
looks a product up by its SKU.
"""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote_plus

import httpx

from core.settings import get_settings
from sources.base import NormalizedOffer

_SHOW = "sku,name,salePrice,url,onlineAvailability"


class BestBuySource:
    name = "bestbuy"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.api_key = get_settings().bestbuy_api_key
        self.base_url = "https://api.bestbuy.com/v1"
        self._http = client or httpx.Client(timeout=10.0)

    def search(self, query: str) -> list[NormalizedOffer]:
        # The (search=...) filter is part of the PATH, not a query param.
        resp = self._http.get(
            f"{self.base_url}/products((search={quote_plus(query)}))",
            params={
                "format": "json",
                "apiKey": self.api_key,
                "show": _SHOW,
                "pageSize": 3,
            },
        )
        resp.raise_for_status()
        products = resp.json().get("products", [])
        return [self._to_offer(p) for p in products if p.get("salePrice") is not None]

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        resp = self._http.get(
            f"{self.base_url}/products/{source_product_id}.json",
            params={"apiKey": self.api_key, "show": _SHOW},
        )
        resp.raise_for_status()
        return self._to_offer(resp.json())

    def _to_offer(self, product: dict) -> NormalizedOffer:
        return NormalizedOffer(
            source=self.name,
            source_product_id=str(product["sku"]),
            title=product.get("name", "Best Buy item"),
            price=Decimal(str(product["salePrice"])),
            currency="USD",  # Best Buy Products API is US-only
            url=product.get("url", ""),
            available=bool(product.get("onlineAvailability", True)),
        )
