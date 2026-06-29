"""Tests for ScraperSource HTML parsing (Phase 5).

No network: we patch the adapter's `_get` to return canned HTML, then assert it
digs the right NormalizedOffer out of the DOM — and that it skips/raises on the
messy, incomplete markup scraping inevitably hits.
"""

import pytest

from sources.scraper import ScraperSource, _parse_price
from decimal import Decimal

CARD = """
<div class="product" data-product-id="sony--store-a">
  <h2 class="title">Sony (store-a)</h2>
  <span class="price" data-currency="USD">$1,199.00</span>
  <a class="buy" href="https://store.test/buy/sony--store-a">Buy now</a>
  <span class="stock">In stock</span>
</div>
"""

SEARCH_HTML = f"<html><body><main class='results'>{CARD}{CARD}</main></body></html>"
PRODUCT_HTML = f"<html><body>{CARD}</body></html>"


def _scraper(html):
    s = ScraperSource(base_url="http://x.test")
    s._get = lambda path, params=None: html  # patch out the HTTP call
    return s


def test_parse_price_handles_messy_strings():
    assert _parse_price("$1,199.00") == Decimal("1199.00")
    assert _parse_price("USD 49.99") == Decimal("49.99")
    assert _parse_price("299") == Decimal("299")
    assert _parse_price(None) is None
    assert _parse_price("call for price") is None


def test_search_parses_cards():
    offers = _scraper(SEARCH_HTML).search("sony")
    assert len(offers) == 2
    o = offers[0]
    assert o.source == "scraper"
    assert o.source_product_id == "sony--store-a"
    assert o.title == "Sony (store-a)"
    assert o.price == Decimal("1199.00")
    assert o.currency == "USD"
    assert o.url == "https://store.test/buy/sony--store-a"
    assert o.available is True


def test_fetch_parses_single_product():
    o = _scraper(PRODUCT_HTML).fetch("sony--store-a")
    assert o.price == Decimal("1199.00")


def test_sold_out_is_unavailable():
    html = PRODUCT_HTML.replace("In stock", "Sold out")
    assert _scraper(html).fetch("sony--store-a").available is False


def test_card_without_price_is_skipped_in_search():
    broken = '<div class="product" data-product-id="x"><a class="buy" href="/x"></a></div>'
    html = f"<html><body>{broken}</body></html>"
    assert _scraper(html).search("x") == []


def test_fetch_without_price_raises():
    # A scrape that finds no price is a failure the worker should retry, not a $0.
    html = "<html><body><div>nothing here</div></body></html>"
    with pytest.raises(ValueError):
        _scraper(html).fetch("x")


def test_parse_price_returns_none_on_unparseable_match():
    # The regex can match characters that aren't a valid number ("$,." -> ".").
    # That must yield None, not raise InvalidOperation.
    assert _parse_price("$,.") is None
    assert _parse_price(",") is None


def test_garbage_price_card_is_skipped_not_crashing_search():
    # One card with a matchable-but-unparseable price must not abort discovery of
    # the whole batch — it's skipped like any other incomplete card.
    broken = (
        '<div class="product" data-product-id="x">'
        '<span class="price">$,.</span>'
        '<a class="buy" href="/x">Buy</a></div>'
    )
    html = f"<html><body>{broken}</body></html>"
    assert _scraper(html).search("x") == []
