"""Tests for real-source adapters: verify they normalize provider payloads into
our `NormalizedOffer` shape. We test the parsing (`_to_offer`) directly with
representative API responses — no network, no credentials needed.
"""

from decimal import Decimal

from sources.bestbuy import BestBuySource
from sources.ebay import EbaySource
from sources.rapidapi import RapidApiProductSource, _parse_price


def test_ebay_to_offer():
    item = {
        "itemId": "v1|123456|0",
        "title": "Sony WH-1000XM5 Headphones",
        "price": {"value": "199.99", "currency": "USD"},
        "itemWebUrl": "https://www.ebay.com/itm/123456",
        "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}],
    }
    offer = EbaySource()._to_offer(item)
    assert offer.source == "ebay"
    assert offer.source_product_id == "v1|123456|0"
    assert offer.price == Decimal("199.99")
    assert offer.currency == "USD"
    assert offer.available is True


def test_ebay_out_of_stock():
    item = {
        "itemId": "v1|9|0",
        "title": "x",
        "price": {"value": "10.00", "currency": "USD"},
        "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "OUT_OF_STOCK"}],
    }
    assert EbaySource()._to_offer(item).available is False


def test_bestbuy_to_offer():
    product = {
        "sku": 6427814,
        "name": "Sony WH-1000XM5",
        "salePrice": 279.99,
        "url": "https://www.bestbuy.com/site/6427814.p",
        "onlineAvailability": True,
    }
    offer = BestBuySource()._to_offer(product)
    assert offer.source == "bestbuy"
    assert offer.source_product_id == "6427814"  # normalized to str
    assert offer.price == Decimal("279.99")
    assert offer.currency == "USD"
    assert offer.available is True


def test_rapidapi_price_parsing():
    assert _parse_price("$1,199.00") == Decimal("1199.00")
    assert _parse_price("£49.99") == Decimal("49.99")
    assert _parse_price("299") == Decimal("299")
    assert _parse_price(None) is None
    assert _parse_price("call for price") is None


def test_rapidapi_to_offer_search_shape():
    # /search products carry `price` at the top level (no nested `offer`) and a
    # google-shopping `product_page_url`. Verified against a live response.
    product = {
        "product_id": "catalogid:3293221069798785293,productid:3511684401561976613",
        "product_title": "AirPods 4 Apple",
        "price": "$129.99",
        "product_page_url": "https://www.google.com/search?ibp=oshop&q=airpods",
        "store_name": "Target",
        "on_sale": False,
    }
    offer = RapidApiProductSource()._to_offer(product)
    assert offer.source == "rapidapi"
    assert offer.source_product_id == (
        "catalogid:3293221069798785293,productid:3511684401561976613"
    )
    assert offer.title == "AirPods 4 Apple"
    assert offer.price == Decimal("129.99")
    assert offer.currency == "USD"
    assert offer.url == "https://www.google.com/search?ibp=oshop&q=airpods"
    assert offer.available is True


def test_rapidapi_to_offer_details_shape():
    # /product-details returns a single product with an `offers` list; the price
    # and the concrete retailer URL live on the first offer. Verified live.
    product = {
        "product_id": "catalogid:3293221069798785293",
        "product_title": "AirPods 4 Apple",
        "product_page_url": "https://www.google.com/search?ibp=oshop&q=airpods",
        "typical_price_range": ["$85", "$130"],
        "offers": [
            {
                "price": "$129.99",
                "offer_page_url": "https://www.target.com/p/ap2022/-/A-85978615",
                "store_name": "Target",
            },
            {"price": "$134.00", "offer_page_url": "https://x", "store_name": "Y"},
        ],
    }
    offer = RapidApiProductSource()._to_offer(product)
    assert offer.source_product_id == "catalogid:3293221069798785293"
    assert offer.price == Decimal("129.99")
    assert offer.currency == "USD"
    assert offer.url == "https://www.target.com/p/ap2022/-/A-85978615"
    assert offer.available is True


def test_rapidapi_skips_unpriced_product():
    product = {"product_id": "x", "product_title": "x", "offers": []}
    assert RapidApiProductSource()._to_offer(product) is None
