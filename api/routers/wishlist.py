"""Wishlist CRUD over a user's tracked products.

Key design decision: **every query is scoped to the current user**. Loading an
item by id alone would be an IDOR vulnerability (one user reading/editing
another's items). So the helper `_get_owned` always filters by both id *and*
user_id, and returns 404 (not 403) for someone else's item — we don't even
confirm it exists.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_current_user
from api.schemas import (
    BestDealOut,
    OfferOut,
    OffersSummary,
    PricePointOut,
    RefreshAccepted,
    TrackedProductCreate,
    TrackedProductOut,
    TrackedProductUpdate,
)
from core.cache import cache_best_deal, get_cached_best_deal
from core.db import get_db
from core.models import Offer, PricePoint, TrackedProduct, User
from core.settings import get_settings
from workers.pipeline import compute_best_deal

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


def _get_owned(item_id: uuid.UUID, user: User, db: Session) -> TrackedProduct:
    item = db.scalar(
        select(TrackedProduct).where(
            TrackedProduct.id == item_id,
            TrackedProduct.user_id == user.id,
        )
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return item


@router.get("", response_model=list[TrackedProductOut])
def list_items(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TrackedProduct]:
    return list(
        db.scalars(
            select(TrackedProduct)
            .where(TrackedProduct.user_id == user.id)
            .order_by(TrackedProduct.created_at.desc())
        )
    )


@router.post("", response_model=TrackedProductOut, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: TrackedProductCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TrackedProduct:
    item = TrackedProduct(user_id=user.id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/{item_id}", response_model=TrackedProductOut)
def get_item(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TrackedProduct:
    return _get_owned(item_id, user, db)


@router.patch("/{item_id}", response_model=TrackedProductOut)
def update_item(
    item_id: uuid.UUID,
    payload: TrackedProductUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TrackedProduct:
    item = _get_owned(item_id, user, db)
    # exclude_unset: only overwrite fields the client actually sent (true PATCH).
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    item = _get_owned(item_id, user, db)
    db.delete(item)
    db.commit()


# --- Offers / price history / manual refresh (Phase 2) ---
@router.get("/{item_id}/offers", response_model=OffersSummary)
def list_offers(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OffersSummary:
    _get_owned(item_id, user, db)  # ownership gate
    offers = list(
        db.scalars(select(Offer).where(Offer.tracked_product_id == item_id))
    )

    # Best deal = cheapest currently-available offer that has a price.
    # (Phase 3 makes this currency-aware + caches it in Redis.)
    priced = [o for o in offers if o.is_available and o.last_price is not None]
    best = min(priced, key=lambda o: o.last_price, default=None)

    return OffersSummary(
        offers=[OfferOut.model_validate(o) for o in offers],
        best_offer_id=best.id if best else None,
        best_price=best.last_price if best else None,
    )


@router.get("/{item_id}/offers/{offer_id}/history", response_model=list[PricePointOut])
def offer_history(
    item_id: uuid.UUID,
    offer_id: uuid.UUID,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PricePoint]:
    _get_owned(item_id, user, db)  # ownership gate via the parent product
    # Filtering by both ids prevents reading history of an offer that isn't
    # under this user's item.
    return list(
        db.scalars(
            select(PricePoint)
            .join(Offer, Offer.id == PricePoint.offer_id)
            .where(
                PricePoint.offer_id == offer_id,
                Offer.tracked_product_id == item_id,
            )
            .order_by(PricePoint.observed_at.desc())
            .limit(limit)
        )
    )


@router.get("/{item_id}/best-deal", response_model=BestDealOut)
def best_deal(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BestDealOut:
    """Cache-aside in action:

    1. Look in Redis for a precomputed best deal.
    2. On a hit, return it (fast — no DB aggregation), flagged `cached: true`.
    3. On a miss, compute from Postgres, store it in Redis with a TTL, return it.

    The worker invalidates this cache key whenever it records a new price, so a
    fresh price always recomputes; otherwise repeated reads are cheap.
    """
    item = _get_owned(item_id, user, db)

    cached = get_cached_best_deal(str(item_id))
    if cached is not None:
        return BestDealOut(**cached, cached=True)

    deal = compute_best_deal(db, item, get_settings().price_history_window_days)
    payload = deal.to_dict()
    cache_best_deal(str(item_id), payload)
    return BestDealOut(**payload, cached=False)


@router.post(
    "/{item_id}/refresh",
    response_model=RefreshAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def refresh_now(
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RefreshAccepted:
    """Enqueue an immediate refresh instead of waiting for the Beat sweep.

    The API just drops a job on the queue and returns 202 — it never does the
    slow fetch inline. Imported lazily so the API doesn't hard-depend on a
    running broker just to import.
    """
    _get_owned(item_id, user, db)
    from workers.tasks import refresh_product

    async_result = refresh_product.delay(str(item_id))
    return RefreshAccepted(task_id=async_result.id)
