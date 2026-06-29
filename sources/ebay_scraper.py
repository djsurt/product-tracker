"""Personal-use eBay HTML scraper adapter.

This is intentionally conservative: it uses public search/product pages, parses
only fields needed by `NormalizedOffer`, and skips incomplete cards rather than
inventing data. It is config-gated separately from the official eBay API source.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from core.settings import get_settings
from sources.base import NormalizedOffer, parse_price
from sources.html_helpers import (
    clean_text,
    first_offer,
    is_available,
    iter_json_ld_products,
)

_ITEM_ID_RE = re.compile(r"/itm/(?:[^/?#]+/)?(\d+)")


class EbayScraperSource:
    name = "ebay_scraper"

    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.ebay_scraper_base_url).rstrip("/")
        self._http = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def search(self, query: str) -> list[NormalizedOffer]:
        html = self._get(
            "/sch/i.html",
            params={"_nkw": query, "_sop": "15", "_ipg": "10"},
        )
        soup = BeautifulSoup(html, "html.parser")
        offers = [self._card_to_offer(card) for card in soup.select(".s-item")]
        return [offer for offer in offers if offer is not None]

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        html = self._get(f"/itm/{source_product_id}")
        soup = BeautifulSoup(html, "html.parser")
        offer = self._json_ld_to_offer(soup, source_product_id)
        if offer is None:
            offer = self._product_dom_to_offer(soup, source_product_id)
        if offer is None:
            raise ValueError(f"ebay_scraper: no price found for {source_product_id}")
        return offer

    def _get(self, path: str, params: dict | None = None) -> str:
        resp = self._http.get(
            urljoin(f"{self.base_url}/", path.lstrip("/")),
            params=params,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
            },
        )
        resp.raise_for_status()
        return resp.text

    def _card_to_offer(self, card) -> NormalizedOffer | None:
        title_el = card.select_one(".s-item__title, [role='heading']")
        title = clean_text(title_el.get_text(" ") if title_el else "")
        if not title or title.lower() == "shop on ebay":
            return None

        link_el = card.select_one("a.s-item__link[href], a[href*='/itm/']")
        href = link_el.get("href") if link_el else None
        item_id = _item_id_from_url(href)
        price_el = card.select_one(".s-item__price")
        price = parse_price(price_el.get_text(" ") if price_el else None)
        if not item_id or not href or price is None:
            return None

        shipping_el = card.select_one(".s-item__shipping, .s-item__logisticsCost")
        shipping_text = clean_text(shipping_el.get_text(" ") if shipping_el else "")
        return NormalizedOffer(
            source=self.name,
            source_product_id=item_id,
            title=title,
            price=price,
            currency="USD",
            url=urljoin(self.base_url, href),
            available=is_available(shipping_text),
        )

    def _json_ld_to_offer(
        self, soup: BeautifulSoup, source_product_id: str
    ) -> NormalizedOffer | None:
        for product in iter_json_ld_products(soup):
            offer = first_offer(product)
            price = parse_price(offer.get("price") or offer.get("lowPrice"))
            if price is None:
                continue
            url = offer.get("url") or f"{self.base_url}/itm/{source_product_id}"
            product_id = clean_text(product.get("sku") or source_product_id)
            return NormalizedOffer(
                source=self.name,
                source_product_id=product_id,
                title=clean_text(product.get("name") or "eBay item"),
                price=price,
                currency=clean_text(offer.get("priceCurrency") or "USD"),
                url=urljoin(self.base_url, str(url)),
                available=is_available(offer.get("availability")),
            )
        return None

    def _product_dom_to_offer(
        self, soup: BeautifulSoup, source_product_id: str
    ) -> NormalizedOffer | None:
        price_el = soup.select_one(
            ".x-price-primary .ux-textspans, [itemprop='price'], .notranslate"
        )
        price = parse_price(price_el.get("content") if price_el else None)
        if price is None and price_el is not None:
            price = parse_price(price_el.get_text(" "))
        if price is None:
            return None

        title_el = soup.select_one(
            "h1.x-item-title__mainTitle span, #itemTitle, h1 span, h1"
        )
        page_text = soup.get_text(" ", strip=True)
        return NormalizedOffer(
            source=self.name,
            source_product_id=source_product_id,
            title=clean_text(title_el.get_text(" ") if title_el else "eBay item"),
            price=price,
            currency="USD",
            url=f"{self.base_url}/itm/{source_product_id}",
            available=is_available(page_text),
        )


def _item_id_from_url(url: object) -> str | None:
    if not url:
        return None
    match = _ITEM_ID_RE.search(str(url))
    return match.group(1) if match else None
