"""A fake storefront we fully control.

Prices *move on their own*: each product's price is a base value (deterministic
from its id, so it's stable across restarts) modulated by a slow sine wave over
time plus small random noise. That means every time the pipeline polls, it sees
a slightly different price — occasionally a new low — which is exactly what makes
the price-tracking demo come alive without any real retailer.

This app has NO database and NO knowledge of users; it's a pure price oracle.
For any query it deterministically yields two "stores" so a wishlist item always
gets offers to compare.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import time

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Mock Store", version="0.1.0")

CURRENCY = "USD"
# Two synthetic storefronts per query so there's always a price to compare.
STORES = ["store-a", "store-b"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def _hash_int(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest(), 16)


def _base_price(product_id: str) -> float:
    """Stable base price in [$50, $500], derived from the id."""
    return 50 + (_hash_int(product_id) % 45_000) / 100.0


def _current_price(product_id: str) -> float:
    """Base price modulated by a slow sine wave (~4 min period) + noise."""
    base = _base_price(product_id)
    phase = _hash_int(product_id) % 1000
    wave = math.sin(time.time() / 40.0 + phase)   # ±1 over ~251s
    seasonal = 1 + 0.12 * wave                     # ±12% swing
    noise = random.uniform(-0.03, 0.03)            # ±3% jitter -> fresh lows
    return round(base * (seasonal + noise), 2)


def _product_id(query: str, store: str) -> str:
    return f"{_slug(query)}--{store}"


def _product_payload(product_id: str, title: str) -> dict:
    return {
        "source_product_id": product_id,
        "title": title,
        "price": _current_price(product_id),
        "currency": CURRENCY,
        "url": f"https://example-store.test/buy/{product_id}",
        "available": True,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/search")
def search(q: str) -> dict:
    results = [
        _product_payload(_product_id(q, store), f"{q} ({store})")
        for store in STORES
    ]
    return {"query": q, "results": results}


@app.get("/products/{source_product_id}")
def get_product(source_product_id: str) -> dict:
    # We accept any id (prices are computed, not stored), but guard against junk.
    if "--" not in source_product_id:
        raise HTTPException(status_code=404, detail="Unknown product")
    title = source_product_id.replace("--", " ").replace("-", " ").title()
    return _product_payload(source_product_id, title)
