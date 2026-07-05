"""Unit tests for WebPageSource — the track-any-store-by-URL adapter.

Canned HTML per extraction layer (JSON-LD, OpenGraph meta, microdata) locks the
contract the pipeline depends on; live pages are too brittle for CI.
"""

from decimal import Decimal

import httpx
import pytest

from sources._http import SourceBlockedError
from sources.webpage import WebPageSource


class FakeClient:
    def __init__(self, html: str, final_url: str | None = None) -> None:
        self.html = html
        self.final_url = final_url
        self.requests: list[str] = []

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        self.requests.append(url)
        return httpx.Response(
            200,
            text=self.html,
            request=httpx.Request("GET", self.final_url or url),
        )


URL = "https://shop.example.com/products/kettle-9000"

JSON_LD_HTML = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product",
 "name": "Kettle 9000",
 "image": ["https://img.example.com/kettle.jpg", "https://img.example.com/alt.jpg"],
 "offers": {"@type": "Offer", "price": "59.99", "priceCurrency": "EUR",
            "availability": "https://schema.org/InStock"}}
</script>
</head><body><h1>Kettle 9000</h1></body></html>
"""

OG_META_HTML = """
<html><head>
<title>Kettle 9000 — Example Shop</title>
<meta property="og:title" content="Kettle 9000">
<meta property="product:price:amount" content="1,299.00">
<meta property="product:price:currency" content="usd">
<meta property="og:availability" content="oos">
<meta property="og:image" content="https://img.example.com/og-kettle.jpg">
</head><body></body></html>
"""

MICRODATA_HTML = """
<html><head><title>Kettle 9000</title></head><body>
<div itemscope itemtype="https://schema.org/Product">
  <span itemprop="name">Kettle 9000</span>
  <span itemprop="price" content="42.50">$42.50</span>
  <meta itemprop="priceCurrency" content="USD">
  <link itemprop="availability" href="https://schema.org/OutOfStock">
</div>
</body></html>
"""

NO_PRICE_HTML = "<html><head><title>A blog post</title></head><body>words</body></html>"


def test_json_ld_is_preferred():
    src = WebPageSource(client=FakeClient(JSON_LD_HTML))
    offer = src.fetch(URL)
    assert offer.source == "web"
    assert offer.source_product_id == URL
    assert offer.title == "Kettle 9000"
    assert offer.price == Decimal("59.99")
    assert offer.currency == "EUR"
    assert offer.available is True
    assert offer.image_url == "https://img.example.com/kettle.jpg"  # first of list


def test_open_graph_meta_fallback():
    offer = WebPageSource(client=FakeClient(OG_META_HTML)).fetch(URL)
    assert offer.price == Decimal("1299.00")
    assert offer.currency == "USD"  # normalized upper-case
    assert offer.title == "Kettle 9000"
    assert offer.image_url == "https://img.example.com/og-kettle.jpg"


def test_microdata_fallback_and_availability():
    offer = WebPageSource(client=FakeClient(MICRODATA_HTML)).fetch(URL)
    assert offer.price == Decimal("42.50")
    assert offer.available is False  # schema.org/OutOfStock


def test_no_structured_price_raises_value_error():
    with pytest.raises(ValueError):
        WebPageSource(client=FakeClient(NO_PRICE_HTML)).fetch(URL)


def test_bot_wall_redirect_fails_loudly():
    blocked = FakeClient(
        "<html>verify you are human</html>",
        final_url="https://us.shein.com/risk/action/limit?risk-id=E1",
    )
    with pytest.raises(SourceBlockedError):
        WebPageSource(client=blocked).fetch("https://us.shein.com/p-123.html")


def test_rejects_non_http_urls():
    with pytest.raises(ValueError):
        WebPageSource(client=FakeClient(JSON_LD_HTML)).fetch("file:///etc/passwd")


def test_search_returns_nothing():
    assert WebPageSource(client=FakeClient(JSON_LD_HTML)).search("kettle") == []
