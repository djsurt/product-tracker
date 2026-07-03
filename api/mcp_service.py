"""User-scoped operations behind the MCP tools (Phase 8).

Plain sync functions taking an explicit Session + User — the same testability
recipe as workers/pipeline.py. The MCP transport layer (api/mcp_server.py)
owns auth, sessions, and threading; this module owns domain behavior. Every
query filters by user_id (see api/routers/wishlist.py for why not-found, not
forbidden).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.jobs import enqueue_product_refresh
from core.models import Alert, Offer, PricePoint, TrackedProduct, User
from core.settings import get_settings
from workers.pipeline import compute_best_deal


class McpServiceError(Exception):
    """A clean, user-presentable tool error (never a stack trace)."""


def _owned_item(db: Session, user: User, item_id: str) -> TrackedProduct:
    try:
        iid = uuid.UUID(item_id)
    except (ValueError, AttributeError, TypeError):
        raise McpServiceError(f"item {item_id!r} not found")
    item = db.scalar(
        select(TrackedProduct).where(
            TrackedProduct.id == iid, TrackedProduct.user_id == user.id
        )
    )
    if item is None:
        raise McpServiceError(f"item {item_id!r} not found")
    return item


def _deal_dict(db: Session, item: TrackedProduct) -> dict:
    deal = compute_best_deal(db, item, get_settings().price_history_window_days)
    # round-trip through json (default=str) to make Decimals/datetimes JSON-safe
    return json.loads(json.dumps(deal.to_dict(), default=str))


def svc_list_wishlist(db: Session, user: User) -> list[dict]:
    items = db.scalars(
        select(TrackedProduct)
        .where(TrackedProduct.user_id == user.id)
        .order_by(TrackedProduct.created_at.desc())
    )
    return [
        {
            "id": str(item.id),
            "title": item.title,
            "query": item.query,
            "target_price": float(item.target_price) if item.target_price is not None else None,
            "is_active": item.is_active,
            "best_deal": _deal_dict(db, item),
        }
        for item in items
    ]


def svc_add_tracked_product(
    db: Session, user: User, title: str, query: str, target_price: float | None = None
) -> dict:
    title = (title or "").strip()
    query = (query or "").strip()
    if not title or not query:
        raise McpServiceError("title and query must both be non-empty")
    if target_price is not None and target_price <= 0:
        raise McpServiceError("target_price must be a positive number")

    item = TrackedProduct(
        user_id=user.id,
        title=title,
        query=query,
        target_price=Decimal(str(target_price)) if target_price is not None else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    enqueue_product_refresh(item.id)
    return {
        "id": str(item.id),
        "title": item.title,
        "query": item.query,
        "target_price": float(item.target_price) if item.target_price is not None else None,
        "note": "tracking started — prices arrive within one refresh cycle",
    }


def svc_get_best_deal(db: Session, user: User, item_id: str) -> dict:
    item = _owned_item(db, user, item_id)
    return _deal_dict(db, item)


def svc_get_price_history(
    db: Session, user: User, item_id: str, limit: int = 50
) -> list[dict]:
    item = _owned_item(db, user, item_id)
    deal = compute_best_deal(db, item, get_settings().price_history_window_days)
    if deal.best_offer_id is None:
        return []
    limit = max(1, min(int(limit), 200))
    rows = db.execute(
        select(PricePoint, Offer.source)
        .join(Offer, Offer.id == PricePoint.offer_id)
        .where(PricePoint.offer_id == deal.best_offer_id)
        .order_by(PricePoint.observed_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "price": float(pp.price),
            "observed_at": pp.observed_at.isoformat(),
            "source": source,
        }
        for pp, source in rows
    ]


def svc_search_deals(query: str, sources: list | None = None) -> dict:
    """Live fan-out across the enabled source adapters.

    Per-source failures degrade gracefully: skip the broken source, report it
    in `failed_sources`, return everything else (mirrors discover_offers).
    """
    query = (query or "").strip()
    if not query:
        raise McpServiceError("query must be non-empty")
    if sources is None:
        from sources.registry import get_sources

        sources = get_sources()

    results: list[dict] = []
    failed: list[str] = []
    for src in sources:
        try:
            found = src.search(query)[:5]  # cap per source: sized for an LLM, not a UI
        except Exception:  # noqa: BLE001 - any source failure means "skip it"
            failed.append(src.name)
            continue
        results.extend(
            {
                "source": o.source,
                "source_product_id": o.source_product_id,
                "title": o.title,
                "price": float(o.price),
                "currency": o.currency,
                "url": o.url,
                "available": o.available,
            }
            for o in found
        )
    return {"results": results, "failed_sources": failed}


def svc_create_alert(
    db: Session, user: User, item_id: str, rule: str, threshold: float | None = None
) -> dict:
    item = _owned_item(db, user, item_id)
    # Mirror the JSON API's guards (api/routers/wishlist.py): never create a
    # rule that could not possibly fire.
    if rule not in ("below_target", "pct_drop"):
        raise McpServiceError("rule must be 'below_target' or 'pct_drop'")
    if rule == "below_target" and threshold is None and item.target_price is None:
        raise McpServiceError(
            "below_target needs a threshold (or set a target_price on the item)"
        )
    if rule == "pct_drop" and threshold is None:
        raise McpServiceError("pct_drop needs a threshold (percentage, e.g. 10)")

    alert = Alert(
        user_id=user.id,
        tracked_product_id=item.id,
        rule=rule,
        threshold=Decimal(str(threshold)) if threshold is not None else None,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return {
        "id": str(alert.id),
        "item_id": str(item.id),
        "rule": alert.rule,
        "threshold": float(alert.threshold) if alert.threshold is not None else None,
        "channel": alert.channel,
    }
