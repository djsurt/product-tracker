"""Small helpers shared by HTML-based source adapters."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def iter_json_ld_products(soup: BeautifulSoup) -> Iterator[dict[str, Any]]:
    """Yield Product objects from JSON-LD script tags.

    Marketplace product pages often expose stable schema.org data even when the
    visible DOM classes churn. We still treat it defensively: invalid JSON or
    unrelated objects are ignored.
    """
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        yield from _find_products(payload)


def _find_products(payload: Any) -> Iterator[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _find_products(item)
        return

    if not isinstance(payload, dict):
        return

    kind = payload.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    if "Product" in kinds:
        yield payload

    graph = payload.get("@graph")
    if graph is not None:
        yield from _find_products(graph)


def first_offer(product: dict[str, Any]) -> dict[str, Any]:
    offers = product.get("offers") or {}
    if isinstance(offers, list):
        return next((o for o in offers if isinstance(o, dict)), {})
    return offers if isinstance(offers, dict) else {}


def is_available(value: object, *, default: bool = True) -> bool:
    if value is None:
        return default
    text = clean_text(value).lower()
    if not text:
        return default
    unavailable_words = ("outofstock", "soldout", "sold out", "unavailable")
    compact = text.replace("_", "").replace("-", "").replace(" ", "")
    return not any(word.replace(" ", "") in compact for word in unavailable_words)
