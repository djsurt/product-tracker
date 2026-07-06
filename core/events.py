"""Redis pub/sub events for live UI updates (Phase 9).

Separate from core/cache.py on purpose: caching is request-path
infrastructure, events are fan-out signaling — but both share the one Redis
instance. Publishing is strictly best-effort: a pub/sub hiccup must never
fail the price pipeline (same stance as api/jobs.enqueue_product_refresh).
"""

from __future__ import annotations

import redis

from core.cache import get_redis
from core.logging import get_logger

log = get_logger(__name__)


def price_update_channel(tracked_product_id: str) -> str:
    return f"price_updates:{tracked_product_id}"


def publish_price_update(
    tracked_product_id: str, client: redis.Redis | None = None
) -> None:
    """Announce that a product's offers changed. Best-effort, never raises."""
    try:
        (client or get_redis()).publish(
            price_update_channel(tracked_product_id), tracked_product_id
        )
    except Exception:  # noqa: BLE001
        log.warning("price_update_publish_failed", tracked_product_id=tracked_product_id)


def model3d_update_channel(tracked_product_id: str) -> str:
    return f"model3d_updates:{tracked_product_id}"


def publish_model3d_update(
    tracked_product_id: str, client: redis.Redis | None = None
) -> None:
    """Announce that a product's 3D model status changed. Best-effort."""
    try:
        (client or get_redis()).publish(
            model3d_update_channel(tracked_product_id), tracked_product_id
        )
    except Exception:  # noqa: BLE001
        log.warning(
            "model3d_update_publish_failed", tracked_product_id=tracked_product_id
        )
