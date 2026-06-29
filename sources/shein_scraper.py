"""Personal-use SHEIN HTML scraper adapter.

SHEIN pages can be JS-heavy. This adapter handles the useful cases where search
or product pages expose server-rendered cards or JSON-LD, and fails loudly on
refresh when no price is present so the existing retry/dead-letter path can act.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin, urlparse

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

_PRODUCT_ID_RE = re.compile(r"-p-(\d+)")


class SheinScraperSource:
    name = "shein_scraper"

    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.shein_scraper_base_url).rstrip("/")
        self._http = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def search(self, query: str) -> list[NormalizedOffer]:
        path = f"/pdsearch/{quote_plus(query)}/"
        html = self._get(path)
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(
            ".S-product-item, .product-list__item, [data-goods-id], [data-product-id]"
        )
        offers = [self._card_to_offer(card) for card in cards]
        return [offer for offer in offers if offer is not None]

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        path = _path_from_source_product_id(source_product_id)
        html = self._get(path)
        soup = BeautifulSoup(html, "html.parser")
        offer = self._json_ld_to_offer(soup, source_product_id)
        if offer is None:
            offer = self._product_dom_to_offer(soup, source_product_id, path)
        if offer is None:
            raise ValueError(f"shein_scraper: no price found for {source_product_id}")
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
        link_el = card.select_one("a[href]") or card.find("a", href=True)
        href = link_el.get("href") if link_el else None
        if not href:
            return None

        title_el = card.select_one(
            ".S-product-item__name, .product-list__item-title, .goods-title-link"
        )
        title = clean_text(
            (title_el.get_text(" ") if title_el else None)
            or link_el.get("title")
            or link_el.get_text(" ")
        )
        price_el = card.select_one(
            ".S-product-item__price, .product-list__item-price, .discount, .price"
        )
        price = parse_price(price_el.get_text(" ") if price_el else None)
        source_product_id = _source_product_id(card, href)
        if not title or price is None or not source_product_id:
            return None

        return NormalizedOffer(
            source=self.name,
            source_product_id=source_product_id,
            title=title,
            price=price,
            currency="USD",
            url=urljoin(self.base_url, href),
            available=is_available(card.get_text(" ", strip=True)),
        )

    def _json_ld_to_offer(
        self, soup: BeautifulSoup, source_product_id: str
    ) -> NormalizedOffer | None:
        for product in iter_json_ld_products(soup):
            offer = first_offer(product)
            price = parse_price(offer.get("price") or offer.get("lowPrice"))
            if price is None:
                continue
            url = offer.get("url") or urljoin(
                self.base_url, _path_from_source_product_id(source_product_id)
            )
            return NormalizedOffer(
                source=self.name,
                source_product_id=source_product_id,
                title=clean_text(product.get("name") or "SHEIN item"),
                price=price,
                currency=clean_text(offer.get("priceCurrency") or "USD"),
                url=urljoin(self.base_url, str(url)),
                available=is_available(offer.get("availability")),
            )
        return None

    def _product_dom_to_offer(
        self, soup: BeautifulSoup, source_product_id: str, path: str
    ) -> NormalizedOffer | None:
        price_el = soup.select_one(
            ".product-intro__head-mainprice, .from, .sale-price, .price"
        )
        price = parse_price(price_el.get_text(" ") if price_el else None)
        if price is None:
            return None

        title_el = soup.select_one(".product-intro__head-name, h1")
        page_text = soup.get_text(" ", strip=True)
        return NormalizedOffer(
            source=self.name,
            source_product_id=source_product_id,
            title=clean_text(title_el.get_text(" ") if title_el else "SHEIN item"),
            price=price,
            currency="USD",
            url=urljoin(self.base_url, path),
            available=is_available(page_text),
        )


def _path_from_source_product_id(source_product_id: str) -> str:
    if source_product_id.startswith("path:"):
        return source_product_id.removeprefix("path:")
    return f"/p-{source_product_id}.html"


def _source_product_id(card, href: object) -> str | None:
    raw_id = card.get("data-goods-id") or card.get("data-product-id")
    if raw_id:
        return f"path:{_normalized_path(href)}"
    path = _normalized_path(href)
    if not path:
        return None
    match = _PRODUCT_ID_RE.search(path)
    return f"path:{path}" if match else None


def _normalized_path(href: object) -> str:
    if not href:
        return ""
    parsed = urlparse(str(href))
    return parsed.path or str(href).split("?", 1)[0]
