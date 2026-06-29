"""Prometheus metrics endpoint (Phase 5).

Exposes the cross-process pipeline counters (see core/metrics.py) in the text
format a Prometheus server scrapes. Unauthenticated on purpose — it carries only
aggregate counts, no user data — and would sit behind network policy in prod.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from core import metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    # Prometheus expects this specific content type/version.
    return PlainTextResponse(
        metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
    )
