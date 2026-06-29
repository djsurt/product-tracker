"""Pure pipeline logic, deliberately separated from the Celery tasks.

The Celery tasks (workers/tasks.py) are thin wrappers that handle scheduling,
retries, and DB sessions. The *actual* work lives here as plain functions taking
an explicit `Session`. That separation means we can unit-test the logic against
SQLite with a fake source — no Redis, no broker, no network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import DeadLetter, Offer, PricePoint, TrackedProduct
from sources.base import NormalizedOffer, PriceSource
from core.logging import get_logger

log = get_logger(__name__)


def discover_offers(
    db: Session, tracked_product: TrackedProduct, sources: list[PriceSource]
) -> list[Offer]:
    """Ensure an Offer row exists for every (source, product) the sources find.

    Idempotent: re-running it updates metadata on existing offers instead of
    creating duplicates (the DB unique constraint is the backstop; we check
    first to keep it portable across Postgres/SQLite).
    """
    for source in sources:
        try:
            found_offers = source.search(tracked_product.query)
        except Exception as exc:  # noqa: BLE001 - one bad source must not block all
            log.warning(
                "discover_offers.source_failed",
                source=source.name,
                tracked_product_id=str(tracked_product.id),
                error=repr(exc),
            )
            continue

        for found in found_offers:
            offer = db.scalar(
                select(Offer).where(
                    Offer.tracked_product_id == tracked_product.id,
                    Offer.source == found.source,
                    Offer.source_product_id == found.source_product_id,
                )
            )
            if offer is None:
                db.add(
                    Offer(
                        tracked_product_id=tracked_product.id,
                        source=found.source,
                        source_product_id=found.source_product_id,
                        title=found.title,
                        url=found.url,
                        currency=found.currency,
                    )
                )
            else:
                # keep mutable metadata fresh (title/url can change upstream)
                offer.title = found.title
                offer.url = found.url

    db.flush()
    return list(
        db.scalars(
            select(Offer).where(Offer.tracked_product_id == tracked_product.id)
        )
    )


def record_price(db: Session, offer: Offer, observed: NormalizedOffer) -> PricePoint:
    """Update the offer's denormalized snapshot AND append to price_history.

    The append is the source of truth; the snapshot is a cache for fast reads.
    """
    now = datetime.now(timezone.utc)
    offer.last_price = observed.price
    offer.last_seen_at = now
    offer.is_available = observed.available

    point = PricePoint(offer_id=offer.id, price=observed.price)
    db.add(point)
    db.flush()
    return point


def get_offer(db: Session, offer_id: str | uuid.UUID) -> Offer | None:
    if isinstance(offer_id, str):
        offer_id = uuid.UUID(offer_id)
    return db.get(Offer, offer_id)


def record_dead_letter(
    db: Session,
    offer_id: str | uuid.UUID | None,
    task_name: str,
    error: str,
    retries: int,
    *,
    mark_offer_unavailable: bool = True,
) -> DeadLetter:
    """Park a permanently-failed task and (optionally) mark its offer stale.

    Marking the offer unavailable means best-deal logic stops trusting its last
    (possibly bogus) price until a future fetch succeeds and revives it — the
    right call when a *fetch* gave up, since the price can no longer be trusted.
    But a failure unrelated to price (e.g. the notify task couldn't reach SMTP)
    must NOT condemn a perfectly good offer, so those callers pass
    `mark_offer_unavailable=False`.
    """
    if isinstance(offer_id, str):
        offer_id = uuid.UUID(offer_id)

    if offer_id is not None and mark_offer_unavailable:
        offer = db.get(Offer, offer_id)
        if offer is not None:
            offer.is_available = False

    dl = DeadLetter(
        offer_id=offer_id,
        task_name=task_name,
        error=error[:4000],  # guard against giant tracebacks
        retries=retries,
    )
    db.add(dl)
    db.flush()
    return dl


@dataclass
class BestDeal:
    """The current best offer for a product, contextualized by history."""

    best_offer_id: uuid.UUID | None
    best_price: Decimal | None
    currency: str | None
    lowest_in_window: Decimal | None     # cheapest we've ever seen in the window
    window_days: int
    verdict: str                          # see _verdict() below

    def to_dict(self) -> dict:
        return {
            "best_offer_id": str(self.best_offer_id) if self.best_offer_id else None,
            "best_price": str(self.best_price) if self.best_price is not None else None,
            "currency": self.currency,
            "lowest_in_window": (
                str(self.lowest_in_window)
                if self.lowest_in_window is not None
                else None
            ),
            "window_days": self.window_days,
            "verdict": self.verdict,
        }


def lowest_in_window(
    db: Session, offer_ids: list[uuid.UUID], days: int
) -> Decimal | None:
    """Cheapest price observed across these offers in the last `days` days.

    This is the query the append-only price_history table exists to answer —
    impossible if we'd only kept each offer's current price.
    """
    if not offer_ids:
        return None
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return db.scalar(
        select(func.min(PricePoint.price)).where(
            PricePoint.offer_id.in_(offer_ids),
            PricePoint.observed_at >= since,
        )
    )


def _verdict(
    best_price: Decimal | None,
    lowest: Decimal | None,
    target_price: Decimal | None,
) -> str:
    if best_price is None:
        return "no_data"
    if target_price is not None and best_price <= target_price:
        return "below_target"          # hit the price the user asked for
    if lowest is not None and best_price <= lowest:
        return "all_time_low"          # at/under the cheapest in the window
    if lowest is not None and best_price <= lowest * Decimal("1.05"):
        return "great"                 # within 5% of the window low
    return "fair"


def compute_best_deal(
    db: Session, tracked_product: TrackedProduct, window_days: int
) -> BestDeal:
    """Pick the cheapest available offer and grade it against recent history."""
    offers = list(
        db.scalars(select(Offer).where(Offer.tracked_product_id == tracked_product.id))
    )
    priced = [o for o in offers if o.is_available and o.last_price is not None]
    # NOTE: this compares raw last_price across offers, which assumes a single
    # currency. Today every active source reports USD; once a source emits a
    # different currency (e.g. RapidAPI returning GBP), picking the numerically
    # smallest price is wrong and this needs an FX-normalized comparison.
    best = min(priced, key=lambda o: o.last_price, default=None)

    low = lowest_in_window(db, [o.id for o in offers], window_days)
    return BestDeal(
        best_offer_id=best.id if best else None,
        best_price=best.last_price if best else None,
        currency=best.currency if best else None,
        lowest_in_window=low,
        window_days=window_days,
        verdict=_verdict(
            best.last_price if best else None, low, tracked_product.target_price
        ),
    )
