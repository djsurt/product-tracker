"""eBay Browse API adapter.

eBay needs an OAuth *application* token (client-credentials grant) before you can
search. Tokens last ~2h, so we cache them in Redis and reuse across all workers
— a real-world example of caching an expensive-to-mint credential rather than
requesting one per call.

Implements the same `PriceSource` interface as the mock store, so the pipeline
treats eBay offers identically to mock offers.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from core.cache import get_redis
from core.settings import get_settings
from sources.base import (
    NormalizedOffer,
    raise_if_listing_gone,
    raise_if_rate_limited,
)

_TOKEN_CACHE_KEY = "ebay:app_token"
_SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbaySource:
    name = "ebay"

    def __init__(self, client: httpx.Client | None = None) -> None:
        s = get_settings()
        self.client_id = s.ebay_client_id
        self.client_secret = s.ebay_client_secret
        self.base_url = (
            "https://api.ebay.com"
            if s.ebay_env == "production"
            else "https://api.sandbox.ebay.com"
        )
        self.marketplace = "EBAY_US"
        self._http = client or httpx.Client(timeout=10.0)

    # --- token management (cached in Redis) ---
    def _access_token(self) -> str:
        r = get_redis()
        cached = r.get(_TOKEN_CACHE_KEY)
        if cached:
            return cached

        resp = self._http.post(
            f"{self.base_url}/identity/v1/oauth2/token",
            data={"grant_type": "client_credentials", "scope": _SCOPE},
            auth=(self.client_id, self.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        body = resp.json()
        token = body["access_token"]
        # Expire our cache a minute early to avoid using a just-expired token.
        ttl = max(60, int(body.get("expires_in", 7200)) - 60)
        r.set(_TOKEN_CACHE_KEY, token, ex=ttl)
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
        }

    # --- PriceSource interface ---
    def search(self, query: str) -> list[NormalizedOffer]:
        resp = self._http.get(
            f"{self.base_url}/buy/browse/v1/item_summary/search",
            params={"q": query, "limit": 3},
            headers=self._headers(),
        )
        raise_if_rate_limited(resp)
        resp.raise_for_status()
        items = resp.json().get("itemSummaries", [])
        return [self._to_offer(it) for it in items if it.get("price")]

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        resp = self._http.get(
            f"{self.base_url}/buy/browse/v1/item/{source_product_id}",
            headers=self._headers(),
        )
        # An ended/removed listing 404s forever — permanent, not retryable.
        raise_if_listing_gone(resp)
        raise_if_rate_limited(resp)
        resp.raise_for_status()
        return self._to_offer(resp.json())

    def _to_offer(self, item: dict) -> NormalizedOffer:
        price = item["price"]
        availability = item.get("estimatedAvailabilities", [{}])
        in_stock = availability[0].get("estimatedAvailabilityStatus") != "OUT_OF_STOCK"
        return NormalizedOffer(
            source=self.name,
            source_product_id=item["itemId"],
            title=item.get("title", "eBay item"),
            price=Decimal(str(price["value"])),
            currency=price["currency"],
            url=item.get("itemWebUrl", ""),
            available=in_stock,
            image_url=(item.get("image") or {}).get("imageUrl")
            or next(
                (
                    t.get("imageUrl")
                    for t in item.get("thumbnailImages") or []
                    if t.get("imageUrl")
                ),
                None,
            ),
        )
