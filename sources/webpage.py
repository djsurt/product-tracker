"""WebPageSource: track any store's product page by URL ("web" source).

Instead of one brittle CSS-selector scraper per retailer, this adapter reads
the *structured data* that product pages publish for search engines — which is
exactly the part of a page that stays stable while visible markup churns:

1. **JSON-LD** (`schema.org/Product`) — emitted by Shopify, WooCommerce,
   BigCommerce, and most large retailers for Google Shopping.
2. **OpenGraph / product meta tags** (`product:price:amount`, `og:title`).
3. **Microdata** (`itemprop="price"`).

If none of those yield a price, we raise instead of guessing from arbitrary
DOM nodes — a wrong price is worse than no price, and the loud failure routes
into the existing retry/dead-letter path.

There is deliberately no anti-bot circumvention here. Fetches go through
PoliteClient (per-host throttle, backoff, honest headers), and hosts that wall
themselves off behind captcha interstitials (e.g. SHEIN's /risk/ redirect) are
detected and fail loudly. For those retailers, use an API (RapidAPI carries
several) rather than fighting the wall.

`search()` returns nothing: this source is fed by users pasting product URLs,
not by query fan-out. The pipeline still refreshes its offers like any other
source because `fetch_offer` goes through `get_source(offer.source).fetch(...)`.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from core.settings import get_settings
from sources._http import PoliteClient, SourceBlockedError
from sources.base import NormalizedOffer, parse_price, raise_if_listing_gone
from sources.html_helpers import (
    clean_text,
    first_offer,
    is_available,
    iter_json_ld_products,
)

# Interstitials that return HTTP 200 but are really a block wall. Matching the
# final (post-redirect) URL catches SHEIN-style soft blocks that would otherwise
# look like "page has no price".
_BLOCK_PATH_RE = re.compile(r"/(risk|captcha|challenge|verify)\b", re.IGNORECASE)

_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


class WebPageSource:
    name = "web"

    def __init__(self, client=None, timeout: float = 10.0) -> None:
        s = get_settings()
        self._http = client or PoliteClient(
            timeout=timeout,
            rate_per_sec=s.scraper_rate_per_sec,
            max_retries=s.scraper_max_retries,
        )

    def search(self, query: str) -> list[NormalizedOffer]:
        return []  # URL-fed source; nothing to fan out to

    def fetch(self, source_product_id: str) -> NormalizedOffer:
        """`source_product_id` IS the product page URL."""
        url = source_product_id.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"web: not a valid product URL: {url!r}")

        resp = self._http.get(url)
        # A removed product page (404/410) is permanent; a bot-wall is not.
        raise_if_listing_gone(resp)
        resp.raise_for_status()
        final_url = str(resp.url)
        if _BLOCK_PATH_RE.search(urlparse(final_url).path):
            raise SourceBlockedError(
                f"{parsed.netloc} redirected to a bot-check page ({final_url})"
            )

        offer = self._extract(resp.text, url)
        if offer is None:
            raise ValueError(f"web: no structured price data found at {url}")
        return offer

    # --- extraction chain ----------------------------------------------------
    def _extract(self, html: str, url: str) -> NormalizedOffer | None:
        soup = BeautifulSoup(html, "html.parser")
        for extractor in (self._from_json_ld, self._from_meta, self._from_microdata):
            found = extractor(soup)
            if found is None:
                continue
            title, raw_price, currency, availability, image = found
            price = parse_price(raw_price)
            if price is None:
                continue  # this layer was present but priceless; try the next
            return NormalizedOffer(
                source=self.name,
                source_product_id=url,
                title=clean_text(title) or urlparse(url).netloc,
                price=price,
                currency=self._currency(currency),
                url=url,
                available=is_available(availability),
                image_url=self._image(image, soup),
            )
        return None

    def _from_json_ld(self, soup: BeautifulSoup):
        for product in iter_json_ld_products(soup):
            offer = first_offer(product)
            raw_price = offer.get("price") or offer.get("lowPrice")
            if raw_price is None:
                continue
            return (
                product.get("name"),
                str(raw_price),
                offer.get("priceCurrency"),
                offer.get("availability"),
                product.get("image"),
            )
        return None

    def _from_meta(self, soup: BeautifulSoup):
        def meta(*props: str) -> str | None:
            for prop in props:
                el = soup.select_one(
                    f'meta[property="{prop}"], meta[name="{prop}"]'
                )
                if el and el.get("content"):
                    return el["content"]
            return None

        raw_price = meta("product:price:amount", "og:price:amount")
        if raw_price is None:
            return None
        title = meta("og:title") or (soup.title.string if soup.title else None)
        return (
            title,
            raw_price,
            meta("product:price:currency", "og:price:currency"),
            meta("product:availability", "og:availability"),
            meta("og:image", "og:image:secure_url"),
        )

    def _from_microdata(self, soup: BeautifulSoup):
        price_el = soup.select_one('[itemprop="price"]')
        if price_el is None:
            return None
        raw_price = price_el.get("content") or price_el.get_text()
        currency_el = soup.select_one('[itemprop="priceCurrency"]')
        avail_el = soup.select_one('[itemprop="availability"]')
        name_el = soup.select_one('[itemprop="name"]')
        title = (
            (name_el.get_text(strip=True) if name_el else None)
            or (soup.title.string if soup.title else None)
        )
        image_el = soup.select_one('[itemprop="image"]')
        image = (
            (image_el.get("src") or image_el.get("content") or image_el.get("href"))
            if image_el
            else None
        )
        return (
            title,
            raw_price,
            currency_el.get("content") if currency_el else None,
            (avail_el.get("href") or avail_el.get("content")) if avail_el else None,
            image,
        )

    def _image(self, raw: object, soup: BeautifulSoup) -> str | None:
        """Normalize the many schema.org image shapes (str | list | ImageObject),
        falling back to og:image so a JSON-LD block without an image doesn't
        cost us one the page still advertises."""
        if isinstance(raw, list):
            raw = next((r for r in raw if r), None)
        if isinstance(raw, dict):  # schema.org ImageObject
            raw = raw.get("url") or raw.get("contentUrl")
        if isinstance(raw, str) and raw.strip().startswith(("http://", "https://")):
            return raw.strip()[:1024]
        og = soup.select_one('meta[property="og:image"], meta[name="og:image"]')
        content = (og.get("content") or "").strip() if og else ""
        return content[:1024] if content.startswith(("http://", "https://")) else None

    @staticmethod
    def _currency(raw: object) -> str:
        text = clean_text(raw).upper()
        return text if _CURRENCY_RE.match(text) else "USD"
