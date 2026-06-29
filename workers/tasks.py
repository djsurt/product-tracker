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
from decimal import Decimal

from celery.exceptions import MaxRetriesExceededError, Retry
from sqlalchemy import select

from core import metrics
from core.cache import RateLimiter, invalidate_best_deal, offer_lock
from core.db import SessionLocal
from core.email import send_email
from core.logging import get_logger
from core.models import TrackedProduct
from core.settings import get_settings
from sources.registry import get_source, get_sources
from workers.celery_app import celery_app
from workers.notifications import fire_alerts
from workers.pipeline import (
    discover_offers,
    get_offer,
    record_dead_letter,
    record_price,
)

settings = get_settings()
log = get_logger(__name__)


def _dead_letter(
    offer_id: str | None,
    task_name: str,
    exc: Exception,
    retries: int,
    *,
    mark_offer_unavailable: bool = True,
) -> bool:
    """Park a permanently-failed task in its own short session.

    The triggering task's session was already rolled back, so we open a fresh
    one. Returns True if the failure was durably recorded, False if even the
    dead-letter write failed (e.g. the DB itself is the outage) — the caller
    uses that to re-raise instead of faking task success, so a permanent failure
    is never swallowed without a trace.

    `mark_offer_unavailable` is forwarded to record_dead_letter: a fetch failure
    condemns the offer's price; a notify (SMTP) failure must not.
    """
    db = SessionLocal()
    try:
        record_dead_letter(
            db, offer_id, task_name, repr(exc), retries,
            mark_offer_unavailable=mark_offer_unavailable,
        )
        db.commit()
        metrics.inc("deal_dead_letter_total")
        log.error("dead_letter", task=task_name, offer_id=offer_id, error=repr(exc))
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("dead_letter_failed", task=task_name, offer_id=offer_id)
        return False
    finally:
        db.close()


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
    tracked_product_id: str | None = None
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
                # Rate-limiting is backpressure, NOT a failure: if we somehow
                # run out of retries waiting for a slot, skip this cycle rather
                # than dead-lettering and condemning a perfectly healthy offer.
                try:
                    raise self.retry(countdown=1)
                except MaxRetriesExceededError:
                    return "skipped: rate-limited"

            tracked_product_id = str(offer.tracked_product_id)
            # Capture the prior price *before* recording so we can tell whether
            # this observation is actually a drop worth notifying about.
            previous_price = offer.last_price
            source = get_source(offer.source)
            observed = source.fetch(offer.source_product_id)
            record_price(db, offer, observed)
            db.commit()

        invalidate_best_deal(tracked_product_id)
        metrics.inc("deal_fetch_success_total")
        log.info(
            "fetch_offer.ok",
            offer_id=offer_id,
            source=observed.source,
            price=str(observed.price),
        )

        # Event-driven decoupling: detection lives here, but the *notification*
        # is a separate task. We only emit the `price_dropped` event when the
        # price genuinely fell — not on the first sighting or a price increase.
        if previous_price is not None and observed.price < previous_price:
            notify.delay(
                tracked_product_id,
                offer_id,
                str(observed.price),
                str(previous_price),
            )
        return str(observed.price)

    except Retry:
        raise  # let Celery handle the scheduled retry; don't treat as failure
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        metrics.inc("deal_fetch_failure_total")
        # Exponential backoff: base * 2**attempt, capped at 60s.
        countdown = min(60, settings.fetch_retry_base_seconds * (2**self.request.retries))
        try:
            metrics.inc("deal_fetch_retry_total")
            log.warning(
                "fetch_offer.retry",
                offer_id=offer_id,
                attempt=self.request.retries,
                error=repr(exc),
            )
            raise self.retry(exc=exc, countdown=countdown)
        except MaxRetriesExceededError:
            # Out of retries: park it in the dead-letter table and mark the
            # offer stale instead of letting the task fail forever.
            if not _dead_letter(offer_id, "fetch_offer", exc, self.request.retries):
                # Couldn't even record the dead-letter (the DB is likely the
                # outage). Surface the real failure instead of returning a
                # success string that would hide it from Celery.
                raise exc
            # The offer was just marked unavailable; drop the cached best deal so
            # reads stop trusting it before the cache TTL would expire.
            if tracked_product_id is not None:
                invalidate_best_deal(tracked_product_id)
            return "dead-lettered"
    finally:
        db.close()


@celery_app.task(bind=True, name="workers.tasks.notify", max_retries=3)
def notify(
    self,
    tracked_product_id: str,
    offer_id: str,
    new_price: str,
    previous_price: str | None,
) -> int:
    """Consume a `price_dropped` event: evaluate alert rules and email the user.

    Kept separate from `fetch_offer` on purpose — the fetch worker shouldn't
    block on (or fail because of) SMTP, and notification has its own retry
    budget. Returns the number of alerts that fired. Email send failures retry
    with backoff so a flaky SMTP server doesn't drop a notification.
    """
    db = SessionLocal()
    try:
        fired = fire_alerts(
            db,
            tracked_product_id=uuid.UUID(tracked_product_id),
            offer_id=uuid.UUID(offer_id),
            new_price=Decimal(new_price),
            previous_price=Decimal(previous_price) if previous_price else None,
            send=send_email,
        )
        db.commit()
        if fired:
            metrics.inc("deal_notify_sent_total", len(fired))
            log.info("notify.sent", offer_id=offer_id, fired=len(fired))
        return len(fired)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        countdown = min(60, settings.fetch_retry_base_seconds * (2**self.request.retries))
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except MaxRetriesExceededError:
            # A notification that couldn't be delivered is a notify failure, not
            # a price problem — never mark the offer unavailable here.
            if not _dead_letter(
                offer_id, "notify", exc, self.request.retries,
                mark_offer_unavailable=False,
            ):
                raise exc
            return 0
    finally:
        db.close()
