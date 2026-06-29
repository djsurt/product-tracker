"""ScraperSource: a price source that parses HTML instead of calling an API.

This is the Phase 5 lesson in *why scraping is fragile*. Unlike the JSON APIs,
there's no contract here — we fish values out of the DOM by CSS selector and
must cope with prices wrapped in currency symbols, markup that drifts, and pages
that simply don't have what we expect. We point it at our own mock store's HTML
(`/html/...`) so we learn parsing/retries without fighting a hostile target.

Tech choice: **httpx + BeautifulSoup** (static HTML). For a JS-rendered site
you'd swap the fetch for **Playwright** (drive a real headless browser) behind
this same interface — the pipeline wouldn't change. We stay with the light path
because the target is static.

Config-gated like the real sources: only active when `SCRAPER_ENABLED=true`.
"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from core.settings import get_settings
# Money parsing is shared across adapters (and guards against malformed prices
# so one bad card can't abort a whole discovery batch). Re-exported under the
# module-local name the tests use.
from sources.base import NormalizedOffer, parse_price as _parse_price


class ScraperSource:
    name = "scraper"

    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        s = get_settings()
        self.base_url = (base_url or s.scraper_base_url or s.mock_store_url).rstrip("/")
        self.timeout = timeout

    def search(self, query: str) -> list[NormalizedOffer]:
        html = self._get("/html/search", params={"q": query})
        soup = BeautifulSoup(html, "html.parser")
        offers = [self._card_to_offer(card) for card in soup.select(".product")]
        return [o for o in offers if o is not None]

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        html = self._get(f"/html/products/{source_product_id}")
        soup = BeautifulSoup(html, "html.parser")
        card = soup.select_one(".product")
        offer = self._card_to_offer(card) if card else None
        if offer is None:
            # A scrape that can't find a price is a failure, not a $0 product —
            # raising lets the worker retry / eventually dead-letter it.
            raise ValueError(f"scraper: no price found for {source_product_id}")
        return offer

    # --- internals ---
    def _get(self, path: str, params: dict | None = None) -> str:
        resp = httpx.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def _card_to_offer(self, card) -> NormalizedOffer | None:
        """Translate one product card element into a NormalizedOffer.

        Defensive: any missing piece (id, price, link) means we skip the card
        rather than emit garbage — scraped pages can't be trusted to be complete.
        """
        if card is None:
            return None
        price_el = card.select_one(".price")
        price = _parse_price(price_el.get_text() if price_el else None)
        product_id = card.get("data-product-id")
        link = card.select_one("a.buy")
        if price is None or not product_id or link is None:
            return None

        title_el = card.select_one(".title")
        stock_el = card.select_one(".stock")
        currency = (price_el.get("data-currency") if price_el else None) or "USD"
        return NormalizedOffer(
            source=self.name,
            source_product_id=product_id,
            title=title_el.get_text(strip=True) if title_el else product_id,
            price=price,
            currency=currency,
            url=link.get("href", ""),
            available=(stock_el.get_text(strip=True).lower() != "sold out")
            if stock_el
            else True,
        )
