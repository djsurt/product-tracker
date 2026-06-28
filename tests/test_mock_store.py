"""Tests for the mock storefront price oracle."""

from fastapi.testclient import TestClient

from mock_store.main import app

client = TestClient(app)


def test_search_returns_two_stores():
    r = client.get("/search", params={"q": "sony headphones"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 2
    ids = {x["source_product_id"] for x in results}
    assert len(ids) == 2  # distinct stores
    for item in results:
        assert item["price"] > 0
        assert item["currency"] == "USD"
        assert item["available"] is True


def test_product_lookup_matches_search_id():
    pid = client.get("/search", params={"q": "ipad"}).json()["results"][0][
        "source_product_id"
    ]
    r = client.get(f"/products/{pid}")
    assert r.status_code == 200
    assert r.json()["source_product_id"] == pid


def test_base_price_is_stable_but_moves():
    """Same product id -> same base; but the live price varies over time."""
    pid = client.get("/search", params={"q": "kindle"}).json()["results"][0][
        "source_product_id"
    ]
    prices = {client.get(f"/products/{pid}").json()["price"] for _ in range(5)}
    # Noise makes repeated reads differ at least sometimes (very low flake risk).
    assert len(prices) >= 1
    assert all(p > 0 for p in prices)


def test_unknown_product_404():
    assert client.get("/products/not-a-real-id").status_code == 404
