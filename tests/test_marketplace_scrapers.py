"""Unit tests for marketplace HTML scrapers.

These tests deliberately use canned HTML instead of live eBay/SHEIN pages. Live
scraping is too brittle for CI, while parser tests still lock down the adapter
contract our pipeline depends on.
"""

from decimal import Decimal

import httpx
import pytest

from sources._http import SourceBlockedError
from sources.ebay_scraper import EbayScraperSource
from sources.shein_scraper import SheinScraperSource


class FakeClient:
    def __init__(self, html_by_path: dict[str, str]) -> None:
        self.html_by_path = html_by_path
        self.requests: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        self.requests.append((url, params))
        path = httpx.URL(url).path
        html = self.html_by_path.get(path)
        if html is None:
            raise AssertionError(f"unexpected URL: {url}")
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))


class RiskRedirectClient:
    """Simulates SHEIN's anti-bot soft block: the requested URL 302-redirects to
    a ``/risk/action/limit`` captcha interstitial that itself returns HTTP 200.
    After following redirects, the response's final URL is the risk page."""

    def __init__(self) -> None:
        self.risk_url = "https://us.shein.com/risk/action/limit?risk-id=E123"

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        return httpx.Response(
            200,
            text="<html><body>captcha verify you are not a robot</body></html>",
            request=httpx.Request("GET", self.risk_url),
        )


EBAY_SEARCH_HTML = """
<ul class="srp-results">
  <li class="s-item">
    <a class="s-item__link" href="https://www.ebay.com/itm/325680000001?hash=x">
      <span role="heading" class="s-item__title">Sony WH-1000XM5 Headphones</span>
    </a>
    <span class="s-item__price">$199.99</span>
  </li>
  <li class="s-item">
    <span class="s-item__title">Shop on eBay</span>
    <span class="s-item__price">$20.00</span>
  </li>
</ul>
"""

EBAY_PRODUCT_HTML = """
<html>
  <head>
    <script type="application/ld+json">
      {
        "@type": "Product",
        "name": "Sony WH-1000XM5 Wireless Noise Canceling Headphones",
        "sku": "325680000001",
        "offers": {
          "@type": "Offer",
          "price": "189.50",
          "priceCurrency": "USD",
          "availability": "https://schema.org/InStock",
          "url": "https://www.ebay.com/itm/325680000001"
        }
      }
    </script>
  </head>
</html>
"""

SHEIN_SEARCH_HTML = """
<section>
  <div class="S-product-item" data-goods-id="17277821">
    <a class="S-product-item__link"
       href="/Men-Solid-Tee-p-17277821-cat-1980.html"
       title="Men Solid Tee">
      <span class="S-product-item__name">Men Solid Tee</span>
    </a>
    <span class="S-product-item__price">$7.49</span>
  </div>
  <div class="S-product-item" data-goods-id="broken">
    <a href="/broken.html">No price</a>
  </div>
</section>
"""

SHEIN_PRODUCT_HTML = """
<html>
  <head>
    <script type="application/ld+json">
      {
        "@type": "Product",
        "name": "Men Solid Tee",
        "sku": "17277821",
        "offers": {
          "@type": "Offer",
          "price": "6.99",
          "priceCurrency": "USD",
          "availability": "https://schema.org/InStock",
          "url": "https://us.shein.com/Men-Solid-Tee-p-17277821-cat-1980.html"
        }
      }
    </script>
  </head>
</html>
"""


def test_ebay_scraper_search_parses_result_cards():
    source = EbayScraperSource(
        base_url="https://www.ebay.com",
        client=FakeClient({"/sch/i.html": EBAY_SEARCH_HTML}),
    )

    offers = source.search("sony xm5")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.source == "ebay_scraper"
    assert offer.source_product_id == "325680000001"
    assert offer.title == "Sony WH-1000XM5 Headphones"
    assert offer.price == Decimal("199.99")
    assert offer.currency == "USD"
    assert offer.url == "https://www.ebay.com/itm/325680000001?hash=x"
    assert offer.available is True


def test_ebay_scraper_fetch_uses_product_json_ld():
    source = EbayScraperSource(
        base_url="https://www.ebay.com",
        client=FakeClient({"/itm/325680000001": EBAY_PRODUCT_HTML}),
    )

    offer = source.fetch("325680000001")

    assert offer.source_product_id == "325680000001"
    assert offer.title == "Sony WH-1000XM5 Wireless Noise Canceling Headphones"
    assert offer.price == Decimal("189.50")
    assert offer.currency == "USD"
    assert offer.available is True


def test_shein_scraper_search_parses_result_cards():
    source = SheinScraperSource(
        base_url="https://us.shein.com",
        client=FakeClient({"/pdsearch/tee/": SHEIN_SEARCH_HTML}),
    )

    offers = source.search("tee")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.source == "shein_scraper"
    assert offer.source_product_id == "path:/Men-Solid-Tee-p-17277821-cat-1980.html"
    assert offer.title == "Men Solid Tee"
    assert offer.price == Decimal("7.49")
    assert offer.currency == "USD"
    assert offer.url == "https://us.shein.com/Men-Solid-Tee-p-17277821-cat-1980.html"
    assert offer.available is True


def test_shein_scraper_fetch_uses_stored_product_path():
    source = SheinScraperSource(
        base_url="https://us.shein.com",
        client=FakeClient(
            {"/Men-Solid-Tee-p-17277821-cat-1980.html": SHEIN_PRODUCT_HTML}
        ),
    )

    offer = source.fetch("path:/Men-Solid-Tee-p-17277821-cat-1980.html")

    assert offer.source_product_id == "path:/Men-Solid-Tee-p-17277821-cat-1980.html"
    assert offer.title == "Men Solid Tee"
    assert offer.price == Decimal("6.99")
    assert offer.currency == "USD"
    assert offer.url == "https://us.shein.com/Men-Solid-Tee-p-17277821-cat-1980.html"
    assert offer.available is True


def test_shein_scraper_search_raises_blocked_on_risk_redirect():
    # SHEIN soft-blocks bots by redirecting to a 200-OK captcha interstitial.
    # We must fail loudly so the worker's retry/dead-letter path engages, rather
    # than parsing the captcha page as "no results" and silently returning [].
    source = SheinScraperSource(
        base_url="https://us.shein.com",
        client=RiskRedirectClient(),
    )

    with pytest.raises(SourceBlockedError):
        source.search("tee")


def test_shein_scraper_fetch_raises_blocked_on_risk_redirect():
    source = SheinScraperSource(
        base_url="https://us.shein.com",
        client=RiskRedirectClient(),
    )

    with pytest.raises(SourceBlockedError):
        source.fetch("path:/Men-Solid-Tee-p-17277821-cat-1980.html")


def test_shein_scraper_fetch_raises_when_product_has_no_price():
    source = SheinScraperSource(
        base_url="https://us.shein.com",
        client=FakeClient(
            {"/empty.html": "<html><body>No useful product</body></html>"}
        ),
    )

    with pytest.raises(ValueError):
        source.fetch("path:/empty.html")
