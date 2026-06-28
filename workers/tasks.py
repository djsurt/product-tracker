"""Celery tasks: thin wrappers around workers/pipeline.py.

The pipeline fans out across three task levels, which demonstrates queue-based
fan-out:

    enqueue_due_refreshes   (Beat fires this on a timer)
        -> refresh_product   (one per active wishlist item)
            -> fetch_offer    (one per offer of that item)

Each level enqueues the next via `.delay(...)`, so work spreads across however
many workers are running. Tasks own their DB session (open/commit/close) and
keep it short-lived — they don't hold a connection across the network call.
"""

from __future__ import annotations

import uuid

from celery.exceptions import Retry
from sqlalchemy import select

from core.cache import RateLimiter, invalidate_best_deal, offer_lock
from core.db import SessionLocal
from core.models import TrackedProduct
from core.settings import get_settings
from sources.registry import get_source, get_sources
from workers.celery_app import celery_app
from workers.pipeline import discover_offers, get_offer, record_price

settings = get_settings()


@celery_app.task(name="workers.tasks.enqueue_due_refreshes")
def enqueue_due_refreshes() -> int:
    """Beat target: enqueue a refresh for every active tracked product."""
    db = SessionLocal()
    try:
        ids = [
            str(tp_id)
            for tp_id in db.scalars(
                select(TrackedProduct.id).where(TrackedProduct.is_active.is_(True))
            )
        ]
    finally:
        db.close()

    for tp_id in ids:
        refresh_product.delay(tp_id)
    return len(ids)


@celery_app.task(name="workers.tasks.refresh_product")
def refresh_product(tracked_product_id: str) -> int:
    """Ensure offers exist for a product, then enqueue a fetch per offer."""
    db = SessionLocal()
    try:
        tp = db.get(TrackedProduct, uuid.UUID(tracked_product_id))
        if tp is None:
            return 0
        offers = discover_offers(db, tp, get_sources())
        db.commit()
        offer_ids = [str(o.id) for o in offers]
    finally:
        db.close()

    for offer_id in offer_ids:
        fetch_offer.delay(offer_id)
    return len(offer_ids)


@celery_app.task(bind=True, name="workers.tasks.fetch_offer", max_retries=5)
def fetch_offer(self, offer_id: str) -> str | None:
    """Re-read one offer's price and record it — now hardened with:

    1. **Idempotency lock**: only one worker fetches a given offer at a time, so
       two overlapping refreshes don't double-write a price point.
    2. **Rate limiting**: we don't exceed N calls/sec to any single source
       (protects our API keys / IP from bans).
    3. **Exponential backoff**: transient failures retry after 2, 4, 8... seconds
       instead of hammering a struggling source.
    4. **Cache invalidation**: after a new price lands, drop the product's cached
       best-deal so the next read recomputes.
    """
    db = SessionLocal()
    try:
        offer = get_offer(db, offer_id)
        if offer is None:
            return None

        with offer_lock(offer_id) as acquired:
            if not acquired:
                # Someone else is already fetching this offer; don't duplicate.
                return "skipped: locked"

            if not RateLimiter().allow(offer.source):
                # Over budget for this source right now — try again shortly.
                raise self.retry(countdown=1)

            tracked_product_id = str(offer.tracked_product_id)
            source = get_source(offer.source)
            observed = source.fetch(offer.source_product_id)
            record_price(db, offer, observed)
            db.commit()

        invalidate_best_deal(tracked_product_id)
        return str(observed.price)

    except Retry:
        raise  # let Celery handle the scheduled retry; don't treat as failure
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # Exponential backoff: base * 2**attempt, capped at 60s.
        countdown = min(60, settings.fetch_retry_base_seconds * (2**self.request.retries))
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()
