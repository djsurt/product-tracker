"""Small queue helpers used by API/web routes."""

from __future__ import annotations

import uuid


def enqueue_product_refresh(item_id: uuid.UUID) -> None:
    """Ask workers to refresh one product without making writes depend on Redis."""
    try:
        from workers.tasks import refresh_product

        refresh_product.apply_async(
            args=(str(item_id),),
            ignore_result=True,
            retry=False,
        )
    except Exception:  # noqa: BLE001
        # Beat will sweep active products later. Creating a wishlist item should
        # not fail just because Redis or the worker is temporarily unavailable.
        pass
