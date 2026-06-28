"""Health and readiness endpoints.

`/health` is a cheap liveness check (process is up). `/health/ready` actually
touches Postgres and Redis — the distinction (liveness vs readiness) matters once
this is deployed behind a load balancer / orchestrator in Phase 6.
"""

from fastapi import APIRouter
from redis import Redis
from sqlalchemy import text

from core.db import engine
from core.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: is the API process responding at all?"""
    return {"status": "ok"}


@router.get("/health/ready")
def ready() -> dict[str, str]:
    """Readiness: can we actually reach our dependencies?"""
    checks: dict[str, str] = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, don't crash the endpoint
        checks["postgres"] = f"error: {exc.__class__.__name__}"

    try:
        Redis.from_url(get_settings().redis_url).ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc.__class__.__name__}"

    checks["status"] = "ok" if all(v == "ok" for k, v in checks.items()) else "degraded"
    return checks
