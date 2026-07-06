"""RapidAPI "Real-Time Product Search" adapter.

This API searches Google Shopping and returns products with real retailer
prices — a great fit for a deal finder, and its signup accepts Gmail. Like the
other adapters it implements `PriceSource` and normalizes into `NormalizedOffer`.

Prices arrive as display strings ("$1,199.00"), so a chunk of this adapter is
just parsing money safely into Decimal. Titles/URLs can be long, so we clamp
them to our column limits.

NOTE: response shapes on RapidAPI vary by version. This targets the documented
shape; once a real key is in place we verify against a live response and adjust
`_to_offer` if needed.
"""

from __future__ import annotations

import httpx

from core.settings import get_settings
# Shared, guarded money parser (see sources/base.py). Re-exported under the
# module-local name the adapter tests import.
from sources.base import (
    NormalizedOffer,
    parse_price as _parse_price,
    raise_if_listing_gone,
)

_CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR"}
_TITLE_MAX = 255
_URL_MAX = 1024


def _detect_currency(raw: object) -> str:
    s = str(raw or "")
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in s:
            return code
    return "USD"


class RapidApiProductSource:
    name = "rapidapi"

    def __init__(self, client: httpx.Client | None = None) -> None:
        s = get_settings()
        self.api_key = s.rapidapi_key
        self.host = s.rapidapi_product_host
        self.country = s.rapidapi_country
        self._http = client or httpx.Client(timeout=15.0)

    def _headers(self) -> dict[str, str]:
        return {"X-RapidAPI-Key": self.api_key or "", "X-RapidAPI-Host": self.host}

    def _url(self, path: str) -> str:
        return f"https://{self.host}{path}"

    # --- PriceSource interface ---
    def search(self, query: str) -> list[NormalizedOffer]:
        resp = self._http.get(
            self._url("/search"),
            params={"q": query, "country": self.country, "limit": 3},
            headers=self._headers(),
        )
        resp.raise_for_status()
        products = self._products(resp.json())
        offers: list[NormalizedOffer] = []
        for product in products[:3]:
            offer = self._to_offer(product)
            if offer is not None:  # skip products with no parseable price
                offers.append(offer)
        return offers

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        resp = self._http.get(
            self._url("/product-details"),
            params={"product_id": source_product_id, "country": self.country},
            headers=self._headers(),
        )
        raise_if_listing_gone(resp)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        # product-details returns a single product object under `data`.
        product = data[0] if isinstance(data, list) else data
        offer = self._to_offer(product)
        if offer is None:
            raise ValueError(f"No parseable price for product {source_product_id}")
        return offer

    # --- helpers ---
    @staticmethod
    def _products(body: dict) -> list[dict]:
        """The product list lives under `data.products` or `data` depending on
        the endpoint/version — handle both."""
        data = body.get("data")
        if isinstance(data, dict):
            return data.get("products", [])
        if isinstance(data, list):
            return data
        return []

    def _to_offer(self, product: dict) -> NormalizedOffer | None:
        # Two live shapes: /search carries `price` at the top level, while
        # /product-details nests price + a concrete retailer URL in an `offers`
        # list. Prefer the top-level price; fall back to the first offer.
        price_raw = product.get("price")
        url = product.get("product_page_url") or ""
        offers = product.get("offers")
        if price_raw is None and isinstance(offers, list) and offers:
            best = offers[0]
            price_raw = best.get("price")
            url = best.get("offer_page_url") or url
        price = _parse_price(price_raw)
        if price is None:
            return None
        return NormalizedOffer(
            source=self.name,
            source_product_id=str(product.get("product_id", "")),
            title=(product.get("product_title") or "Product")[:_TITLE_MAX],
            price=price,
            currency=_detect_currency(price_raw),
            url=url[:_URL_MAX],
            available=True,
            image_url=next(
                (p for p in product.get("product_photos") or [] if isinstance(p, str)),
                None,
            ),
        )
