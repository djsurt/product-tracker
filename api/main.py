"""FastAPI application entrypoint.

Kept deliberately thin: it wires routers and app-level config, nothing more.
Business logic lives in routers/services, slow work lives in Celery workers.
Run locally with: uvicorn api.main:app --reload
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from api import mcp_server
from api.deps import get_current_user
from api.routers import auth, health, metrics, redirect, web, wishlist
from api.schemas import UserOut
from core.logging import configure_logging
from core.models import User
from core.settings import get_settings

settings = get_settings()
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The MCP session manager must be running for /mcp to serve requests.
    async with mcp_server.mcp_lifespan():
        yield


app = FastAPI(
    title="Deal Hunter API",
    version="0.1.0",
    description="Wishlist price tracking + best-deal finder.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(auth.router)
app.include_router(wishlist.router)
app.include_router(redirect.router)
app.include_router(web.router)
app.add_middleware(mcp_server.McpPathASGI)

# Vendored front-end assets (model-viewer). Self-hosted, never hot-linked.
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)


@app.get("/")
def root() -> dict:
    from sources.registry import active_source_names

    return {
        "app": "deal-hunter",
        "env": settings.env,
        "docs": "/docs",
        "ui": "/app",
        "active_sources": active_source_names(),
    }


@app.get("/me", response_model=UserOut, tags=["auth"])
def me(user: User = Depends(get_current_user)) -> User:
    """Return the currently authenticated user (handy for verifying a token)."""
    return user
