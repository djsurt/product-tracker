# Remote MCP Server + SSE Live Prices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a token-authenticated MCP Streamable-HTTP endpoint at `/mcp` on the live deployment (6 user-scoped tools over the existing wishlist domain), and replace the item page's 5-second HTMX polling with Server-Sent Events driven by Redis pub/sub.

**Architecture:** Phase A mounts a FastMCP server inside the existing FastAPI app behind a raw-ASGI bearer-token middleware; tools are thin async wrappers over a new sync service layer (`api/mcp_service.py`) that reuses the existing ownership-gated query patterns. Phase B publishes to a Redis channel from the worker right where prices are recorded, and a new async endpoint re-renders the existing `_offers.html` fragment per message into an SSE stream consumed by the HTMX SSE extension.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Celery, Redis (sync `redis` + `redis.asyncio`), `mcp` Python SDK (FastMCP), HTMX + `htmx-ext-sse`, pytest + fakeredis + sqlite.

**Spec:** `docs/superpowers/specs/2026-07-02-mcp-server-sse-live-prices-design.md`

## Global Constraints

- Token format: `dh_live_` + `secrets.token_urlsafe(32)`; stored only as SHA-256 hex digest; plaintext shown exactly once at creation.
- MCP endpoint path: `/mcp` (mounted); auth header: `Authorization: Bearer dh_live_…`; invalid/missing token → HTTP 401 with `WWW-Authenticate: Bearer`. OAuth is out of scope.
- Exactly 6 MCP tools: `list_wishlist`, `add_tracked_product`, `get_best_deal`, `get_price_history`, `search_deals`, `create_alert`. Tool errors are raised as exceptions with clean messages (never stack traces in the message); `search_deals` degrades per-source (partial results + `failed_sources`).
- Redis pub/sub channel: `price_updates:{tracked_product_id}`; SSE event name: `offers`; heartbeat comment every 15s; SSE response headers include `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- New deps (append to `requirements.txt`): `mcp>=1.10`, `anyio>=4.5`. No other new dependencies.
- All new DB queries touching user data must filter by `user_id` (follow `_get_owned` / `_owned` pattern); non-owned rows return "not found", never "forbidden".
- Money stays `Decimal` in the DB layer; MCP tool outputs convert to `float`/`str` (JSON-friendly).
- Tests run with `python -m pytest` from the repo root against sqlite/fakeredis — no Postgres, Redis, or network.
- Every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

# Phase A — Remote MCP server

### Task 1: `ApiToken` model + Alembic migration

**Files:**
- Modify: `core/models.py` (append new model at end of file)
- Create: `migrations/versions/0005_api_tokens.py`
- Test: `tests/test_api_tokens.py`

**Interfaces:**
- Produces: `core.models.ApiToken` with columns `id: uuid PK`, `user_id: uuid FK users.id (CASCADE)`, `name: str(100)`, `token_hash: str(64) unique`, `created_at: datetime`, `last_used_at: datetime | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_tokens.py`:

```python
"""Tests for API tokens (Phase 8): model + generate/hash/resolve helpers."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import ApiToken, User

import core.models  # noqa: F401  (register tables on Base.metadata)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def user(db):
    u = User(email="t@example.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


def test_api_token_round_trip(db, user):
    row = ApiToken(user_id=user.id, name="my-laptop", token_hash="a" * 64)
    db.add(row)
    db.commit()

    got = db.scalar(select(ApiToken).where(ApiToken.token_hash == "a" * 64))
    assert got is not None
    assert got.user_id == user.id
    assert got.name == "my-laptop"
    assert isinstance(got.created_at, datetime)
    assert got.last_used_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_tokens.py -v`
Expected: FAIL with `ImportError: cannot import name 'ApiToken'`

- [ ] **Step 3: Add the model**

Append to `core/models.py` (after `DeadLetter`):

```python
class ApiToken(Base):
    """A personal access token for the remote MCP endpoint (Phase 8).

    We store only a SHA-256 digest of the token — the plaintext is shown once
    at creation and never again (the GitHub PAT pattern), so a DB leak doesn't
    hand out working credentials. `last_used_at` lets the UI show whether a
    token is actually in use before the user revokes it.
    """

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
```

- [ ] **Step 4: Create the migration**

Create `migrations/versions/0005_api_tokens.py` (mirrors `0004_dead_letters.py` style):

```python
"""api_tokens

Revision ID: 0005_api_tokens
Revises: 0004_dead_letters
Create Date: 2026-07-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_api_tokens"
down_revision = "0004_dead_letters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_table("api_tokens")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_tokens.py -v`
Expected: PASS (1 test)

Run: `python -m pytest -q`
Expected: full suite PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add core/models.py migrations/versions/0005_api_tokens.py tests/test_api_tokens.py
git commit -m "Add ApiToken model + migration for MCP personal access tokens

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Token helpers (`core/tokens.py`)

**Files:**
- Create: `core/tokens.py`
- Test: `tests/test_api_tokens.py` (append)

**Interfaces:**
- Consumes: `core.models.ApiToken`, `core.models.User` (Task 1).
- Produces:
  - `core.tokens.TOKEN_PREFIX: str = "dh_live_"`
  - `core.tokens.generate_token() -> tuple[str, str]` — `(plaintext, sha256_hex)`
  - `core.tokens.hash_token(plain: str) -> str`
  - `core.tokens.resolve_token(db: Session, plain: str | None) -> User | None` — also stamps `last_used_at` and commits.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_tokens.py`:

```python
from core.tokens import TOKEN_PREFIX, generate_token, hash_token, resolve_token


def test_generate_token_shape():
    plain, digest = generate_token()
    assert plain.startswith(TOKEN_PREFIX)
    assert len(plain) > len(TOKEN_PREFIX) + 30
    assert digest == hash_token(plain)
    assert len(digest) == 64  # sha256 hex


def test_resolve_token_finds_user_and_stamps_last_used(db, user):
    plain, digest = generate_token()
    db.add(ApiToken(user_id=user.id, name="t", token_hash=digest))
    db.commit()

    resolved = resolve_token(db, plain)
    assert resolved is not None
    assert resolved.id == user.id

    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
    assert row.last_used_at is not None


def test_resolve_token_rejects_unknown_and_malformed(db, user):
    plain, digest = generate_token()
    db.add(ApiToken(user_id=user.id, name="t", token_hash=digest))
    db.commit()

    assert resolve_token(db, None) is None
    assert resolve_token(db, "") is None
    assert resolve_token(db, "not-a-token") is None                      # no prefix
    assert resolve_token(db, TOKEN_PREFIX + "wrong-suffix") is None      # unknown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.tokens'`

- [ ] **Step 3: Implement `core/tokens.py`**

```python
"""Personal access tokens for the remote MCP endpoint (Phase 8).

Same threat model as GitHub PATs: the plaintext exists only in the creation
response; we persist a SHA-256 digest and compare digests on every request.
SHA-256 (not bcrypt) is fine here because the token itself is high-entropy
random — there's nothing to brute-force offline the way there is with a
human-chosen password.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import ApiToken, User

TOKEN_PREFIX = "dh_live_"


def generate_token() -> tuple[str, str]:
    """Return (plaintext, sha256_hex). Plaintext is shown once, never stored."""
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plain, hash_token(plain)


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def resolve_token(db: Session, plain: str | None) -> User | None:
    """Look up the user owning a presented token, or None.

    The prefix check is a cheap reject for obviously-wrong values (and stray
    JWTs) before we bother hashing. Stamps `last_used_at` so the UI can show
    whether a token is live before the user revokes it.
    """
    if not plain or not plain.startswith(TOKEN_PREFIX):
        return None
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(plain)))
    if row is None:
        return None
    # naive UTC to match the server_default(func.now()) columns
    row.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return db.get(User, row.user_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_tokens.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core/tokens.py tests/test_api_tokens.py
git commit -m "Add token generate/hash/resolve helpers (sha256, shown-once)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: MCP service layer (`api/mcp_service.py`)

User-scoped, plain-sync business functions — everything the MCP tools do, minus transport/auth. Testable with a bare sqlite Session.

**Files:**
- Create: `api/mcp_service.py`
- Test: `tests/test_mcp_service.py`

**Interfaces:**
- Consumes: `core.models`, `workers.pipeline.compute_best_deal(db, item, window_days)` (returns object with `.best_offer_id` and `.to_dict()`), `sources.registry.get_sources()`, `api.jobs.enqueue_product_refresh(item_id)`, `sources.base.NormalizedOffer`.
- Produces (all raise `McpServiceError` with a clean message on bad input / not-found):
  - `McpServiceError(Exception)`
  - `svc_list_wishlist(db, user) -> list[dict]`
  - `svc_add_tracked_product(db, user, title: str, query: str, target_price: float | None = None) -> dict`
  - `svc_get_best_deal(db, user, item_id: str) -> dict`
  - `svc_get_price_history(db, user, item_id: str, limit: int = 50) -> list[dict]`
  - `svc_search_deals(query: str, sources: list | None = None) -> dict` — `{"results": [...], "failed_sources": [...]}`
  - `svc_create_alert(db, user, item_id: str, rule: str, threshold: float | None = None) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_service.py`:

```python
"""Tests for the MCP service layer (Phase 8): user-scoped wishlist operations.

Same philosophy as test_pipeline.py — plain functions, explicit Session, fake
sources. Transport and auth are tested separately in test_mcp_server.py.
"""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.mcp_service import (
    McpServiceError,
    svc_add_tracked_product,
    svc_create_alert,
    svc_get_best_deal,
    svc_get_price_history,
    svc_list_wishlist,
    svc_search_deals,
)
from core.db import Base
from core.models import Alert, Offer, PricePoint, TrackedProduct, User
from sources.base import NormalizedOffer

import core.models  # noqa: F401


class FakeSource:
    name = "fake"

    def search(self, query):
        return [
            NormalizedOffer(
                source=self.name,
                source_product_id=f"{query}-1",
                title=f"{query} deluxe",
                price=Decimal("89.99"),
                currency="USD",
                url="http://x.test/buy/1",
                available=True,
            )
        ]

    def fetch(self, source_product_id):  # pragma: no cover - not used here
        raise NotImplementedError


class BrokenSource:
    name = "broken"

    def search(self, query):
        raise TimeoutError("source timed out")

    def fetch(self, source_product_id):  # pragma: no cover - not used here
        raise NotImplementedError


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def user(db):
    u = User(email="mcp@example.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def other_user(db):
    u = User(email="other@example.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def item_with_offer(db, user):
    tp = TrackedProduct(
        user_id=user.id, title="Headphones", query="headphones",
        target_price=Decimal("250.00"),
    )
    db.add(tp)
    db.flush()
    offer = Offer(
        tracked_product_id=tp.id, source="fake", source_product_id="h1",
        title="Headphones", url="http://x.test/buy/h1", currency="USD",
        last_price=Decimal("199.99"), is_available=True,
    )
    db.add(offer)
    db.flush()
    for price in ("219.99", "209.99", "199.99"):
        db.add(PricePoint(offer_id=offer.id, price=Decimal(price)))
    db.commit()
    return tp


def test_list_wishlist_includes_best_deal(db, user, item_with_offer):
    items = svc_list_wishlist(db, user)
    assert len(items) == 1
    assert items[0]["title"] == "Headphones"
    assert items[0]["best_deal"]["best_price"] == "199.99"


def test_list_wishlist_is_user_scoped(db, user, other_user, item_with_offer):
    assert svc_list_wishlist(db, other_user) == []


def test_add_tracked_product_creates_and_enqueues(db, user, monkeypatch):
    enqueued = []
    monkeypatch.setattr(
        "api.mcp_service.enqueue_product_refresh", lambda item_id: enqueued.append(item_id)
    )
    out = svc_add_tracked_product(db, user, "PS5", "playstation 5", 399.99)
    assert out["title"] == "PS5"
    assert out["target_price"] == 399.99

    row = db.scalar(select(TrackedProduct).where(TrackedProduct.title == "PS5"))
    assert row is not None and row.user_id == user.id
    assert enqueued == [row.id]


def test_add_tracked_product_rejects_blank_and_negative(db, user):
    with pytest.raises(McpServiceError):
        svc_add_tracked_product(db, user, "", "query")
    with pytest.raises(McpServiceError):
        svc_add_tracked_product(db, user, "Thing", "   ")
    with pytest.raises(McpServiceError):
        svc_add_tracked_product(db, user, "Thing", "thing", -5)


def test_get_best_deal_owned(db, user, item_with_offer):
    deal = svc_get_best_deal(db, user, str(item_with_offer.id))
    assert deal["best_price"] == "199.99"
    assert "verdict" in deal


def test_get_best_deal_not_owned_or_malformed(db, user, other_user, item_with_offer):
    with pytest.raises(McpServiceError, match="not found"):
        svc_get_best_deal(db, other_user, str(item_with_offer.id))
    with pytest.raises(McpServiceError, match="not found"):
        svc_get_best_deal(db, user, "not-a-uuid")


def test_get_price_history_returns_best_offer_series(db, user, item_with_offer):
    points = svc_get_price_history(db, user, str(item_with_offer.id), limit=2)
    assert len(points) == 2
    assert points[0]["price"] == 199.99  # newest first
    assert points[0]["source"] == "fake"


def test_search_deals_partial_failure(db):
    out = svc_search_deals("headphones", sources=[FakeSource(), BrokenSource()])
    assert out["failed_sources"] == ["broken"]
    assert len(out["results"]) == 1
    assert out["results"][0]["price"] == 89.99
    assert out["results"][0]["source"] == "fake"


def test_create_alert_valid_and_invalid(db, user, item_with_offer):
    out = svc_create_alert(db, user, str(item_with_offer.id), "pct_drop", 10)
    assert out["rule"] == "pct_drop"
    assert db.scalar(select(Alert)) is not None

    with pytest.raises(McpServiceError):
        svc_create_alert(db, user, str(item_with_offer.id), "bogus_rule", 10)
    with pytest.raises(McpServiceError):
        svc_create_alert(db, user, str(item_with_offer.id), "pct_drop", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.mcp_service'`

- [ ] **Step 3: Implement `api/mcp_service.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_service.py -v`
Expected: PASS (9 tests)

Note: `test_list_wishlist_includes_best_deal` asserts `best_price == "199.99"` (string) because `_deal_dict` stringifies Decimals via `default=str`. If `compute_best_deal(...).to_dict()` turns out to already return floats, change the assertion to `== 199.99` — check `workers/pipeline.py` `to_dict()` if this assertion fails, and match reality.

- [ ] **Step 5: Commit**

```bash
git add api/mcp_service.py tests/test_mcp_service.py
git commit -m "Add MCP service layer: user-scoped wishlist ops for tools

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: MCP server + token auth + mount (`api/mcp_server.py`, `api/main.py`)

**Files:**
- Create: `api/mcp_server.py`
- Modify: `api/main.py` (lifespan + mount)
- Modify: `requirements.txt` (add `mcp>=1.10`, `anyio>=4.5`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `core.tokens.resolve_token` (Task 2), all `svc_*` functions and `McpServiceError` (Task 3).
- Produces:
  - `api.mcp_server.mcp: FastMCP` (named `"deal-hunter"`) with exactly 6 tools.
  - `api.mcp_server.mcp_asgi_app` — ASGI app (auth middleware wrapping the MCP endpoint) mounted at `/mcp`.
  - `api.mcp_server.mcp_lifespan()` — async context manager; creates a **fresh** `StreamableHTTPSessionManager` per entry (a manager can only `.run()` once, and tests enter the app lifespan once per test).
  - `api.mcp_server._open_session()` — the module's only DB-session seam; tests monkeypatch it.

**Install first:** `pip install "mcp>=1.10"` and append to `requirements.txt`:

```
# Remote MCP server (Phase 8): Streamable-HTTP endpoint mounted at /mcp.
# anyio is already a transitive dep (starlette), pinned here because we
# import it directly for thread offloading.
mcp>=1.10
anyio>=4.5
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_server.py`:

```python
"""Tests for the MCP transport layer (Phase 8): token auth + end-to-end tools.

The end-to-end test drives a real MCP client over Streamable HTTP through
httpx's ASGITransport — no sockets — which exercises the exact production
path: auth middleware -> session manager -> tool -> service -> sqlite.
"""

from functools import partial

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import mcp_server
from api.main import app
from core.db import Base, get_db
from core.models import ApiToken, TrackedProduct, User
from core.tokens import generate_token

import core.models  # noqa: F401


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mcp_env(monkeypatch):
    """sqlite DB shared by the app *and* the MCP server's own session seam."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("api.mcp_server._open_session", lambda: TestingSessionLocal())
    yield TestingSessionLocal
    app.dependency_overrides.clear()


@pytest.fixture
def token(mcp_env):
    """A user + a valid API token; returns (plaintext, user_id)."""
    db = mcp_env()
    user = User(email="mcp-user@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    plain, digest = generate_token()
    db.add(ApiToken(user_id=user.id, name="test", token_hash=digest))
    db.commit()
    user_id = user.id
    db.close()
    return plain, user_id


PING = {"jsonrpc": "2.0", "method": "ping", "id": 1}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_mcp_requires_token(mcp_env):
    with TestClient(app) as client:
        resp = client.post("/mcp", json=PING, headers=MCP_HEADERS)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_mcp_rejects_bad_token(mcp_env):
    with TestClient(app) as client:
        resp = client.post(
            "/mcp", json=PING,
            headers={**MCP_HEADERS, "Authorization": "Bearer dh_live_nope"},
        )
    assert resp.status_code == 401


def test_mcp_accepts_valid_token(mcp_env, token):
    plain, _ = token
    with TestClient(app) as client:
        resp = client.post(
            "/mcp", json=PING,
            headers={**MCP_HEADERS, "Authorization": f"Bearer {plain}"},
        )
    # Past auth: whatever the protocol says about a bare ping, it isn't a 401.
    assert resp.status_code != 401


@pytest.mark.anyio
async def test_mcp_client_end_to_end(mcp_env, token):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    plain, user_id = token

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
        )

    async with mcp_server.mcp_lifespan():
        async with streamablehttp_client(
            "http://testserver/mcp",
            headers={"Authorization": f"Bearer {plain}"},
            httpx_client_factory=factory,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = {t.name for t in (await session.list_tools()).tools}
                assert tools == {
                    "list_wishlist", "add_tracked_product", "get_best_deal",
                    "get_price_history", "search_deals", "create_alert",
                }

                added = await session.call_tool(
                    "add_tracked_product",
                    {"title": "PS5", "query": "playstation 5", "target_price": 399.99},
                )
                assert not added.isError
                assert "PS5" in added.content[0].text

                listed = await session.call_tool("list_wishlist", {})
                assert not listed.isError
                assert "PS5" in listed.content[0].text

                # unknown item -> clean tool error, not a crash
                bad = await session.call_tool("get_best_deal", {"item_id": "not-a-uuid"})
                assert bad.isError
                assert "not found" in bad.content[0].text

    # the tool wrote through the MCP server's own session seam, scoped to our user
    db = mcp_env()
    row = db.scalar(select(TrackedProduct).where(TrackedProduct.title == "PS5"))
    assert row is not None and row.user_id == user_id
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ImportError` (`api.mcp_server` doesn't exist)

- [ ] **Step 3: Implement `api/mcp_server.py`**

```python
"""Remote MCP server (Phase 8), mounted at /mcp on the existing app.

Layers, outside-in:

1. `TokenAuthASGI` — raw ASGI middleware. Resolves `Authorization: Bearer
   dh_live_…` to a user (401 otherwise) and stashes the user id in a
   contextvar for the duration of the request.
2. `_McpEndpoint` — delegates to the StreamableHTTPSessionManager owned by
   the *current* lifespan. A manager can only `.run()` once, and TestClient
   enters the app lifespan once per test, so `mcp_lifespan()` builds a fresh
   manager each time instead of reusing FastMCP's cached one.
3. `@mcp.tool()` wrappers — read the user id, then run the sync service
   function on a worker thread with its own short-lived DB session.

Auth is bearer-token, not OAuth, by design: Claude Code / Desktop connect
with a --header flag; see the spec for the trade-off.
"""

from __future__ import annotations

import contextlib
import contextvars
import uuid
from functools import partial

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from api import mcp_service
from core.models import User
from core.tokens import resolve_token

_current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_user_id", default=None
)

mcp = FastMCP("deal-hunter")


# --- session + user plumbing ------------------------------------------------
def _open_session():
    """The module's only DB seam — tests monkeypatch this."""
    from core.db import SessionLocal

    return SessionLocal()


def _require_user_id() -> str:
    uid = _current_user_id.get()
    if uid is None:  # unreachable behind the middleware; guards direct calls
        raise mcp_service.McpServiceError("not authenticated")
    return uid


def _with_user_db(user_id: str, fn, /, *args, **kwargs):
    """Run a service function with a fresh session + the resolved user."""
    db = _open_session()
    try:
        user = db.get(User, uuid.UUID(user_id))
        if user is None:
            raise mcp_service.McpServiceError("not authenticated")
        return fn(db, user, *args, **kwargs)
    finally:
        db.close()


async def _run(fn, *args, **kwargs):
    """Offload a user-scoped sync service call to a worker thread."""
    uid = _require_user_id()  # read the contextvar *before* changing threads
    return await anyio.to_thread.run_sync(partial(_with_user_db, uid, fn, *args, **kwargs))


# --- the 6 tools --------------------------------------------------------------
@mcp.tool()
async def list_wishlist() -> list[dict]:
    """List every product on the user's wishlist, each with its current best
    deal (best price, source, and a verdict like 'all_time_low' or 'fair')."""
    return await _run(mcp_service.svc_list_wishlist)


@mcp.tool()
async def add_tracked_product(
    title: str, query: str, target_price: float | None = None
) -> dict:
    """Start tracking a product. `title` is a display name, `query` is what we
    search stores for (e.g. 'sony wh-1000xm5'), and `target_price` (optional)
    is the price at which the user wants to be alerted."""
    return await _run(mcp_service.svc_add_tracked_product, title, query, target_price)


@mcp.tool()
async def get_best_deal(item_id: str) -> dict:
    """Current best deal for one wishlist item: cheapest live offer, whether
    it's the lowest in the last 30 days, and a buy/wait verdict."""
    return await _run(mcp_service.svc_get_best_deal, item_id)


@mcp.tool()
async def get_price_history(item_id: str, limit: int = 50) -> list[dict]:
    """Recent observed prices (newest first) for the best offer of one
    wishlist item — use this to see the trend before buying."""
    return await _run(mcp_service.svc_get_price_history, item_id, limit)


@mcp.tool()
async def search_deals(query: str) -> dict:
    """Search all enabled stores live for a product and return current offers
    with prices. Sources that fail are listed in `failed_sources`."""
    _require_user_id()  # auth-only: search has no per-user state
    return await anyio.to_thread.run_sync(partial(mcp_service.svc_search_deals, query))


@mcp.tool()
async def create_alert(
    item_id: str, rule: str, threshold: float | None = None
) -> dict:
    """Create a price alert on a wishlist item. rule='below_target' emails
    when the best price drops to `threshold` (or the item's target_price);
    rule='pct_drop' emails on any drop of `threshold` percent."""
    return await _run(mcp_service.svc_create_alert, item_id, rule, threshold)


# --- transport: endpoint + lifespan + auth ------------------------------------
class _McpEndpoint:
    """ASGI endpoint delegating to the manager created by mcp_lifespan()."""

    def __init__(self) -> None:
        self.manager: StreamableHTTPSessionManager | None = None

    async def __call__(self, scope, receive, send) -> None:
        if self.manager is None:
            await JSONResponse(
                {"error": "MCP server is not running"}, status_code=503
            )(scope, receive, send)
            return
        await self.manager.handle_request(scope, receive, send)


_endpoint = _McpEndpoint()


@contextlib.asynccontextmanager
async def mcp_lifespan():
    # NOTE: `mcp._mcp_server` is the SDK's low-level server behind FastMCP.
    # It's a private attribute, used deliberately: FastMCP's own
    # streamable_http_app() caches one session manager forever, and a manager
    # can only .run() once per instance — which breaks any process that
    # cycles the app lifespan (every TestClient context). If an SDK upgrade
    # renames it, tests/test_mcp_server.py fails loudly.
    manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server, stateless=True, json_response=True
    )
    _endpoint.manager = manager
    try:
        async with manager.run():
            yield
    finally:
        _endpoint.manager = None


def _resolve_user_id(token: str) -> str | None:
    db = _open_session()
    try:
        user = resolve_token(db, token)
        return str(user.id) if user is not None else None
    finally:
        db.close()


class TokenAuthASGI:
    """Bearer-token gate in front of the MCP endpoint."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        auth = Headers(scope=scope).get("authorization") or ""
        token = auth.removeprefix("Bearer ").strip()
        user_id = await anyio.to_thread.run_sync(partial(_resolve_user_id, token))
        if user_id is None:
            await JSONResponse(
                {"error": "missing or invalid API token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        ctx_token = _current_user_id.set(user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user_id.reset(ctx_token)


mcp_asgi_app = TokenAuthASGI(_endpoint)
```

- [ ] **Step 4: Wire lifespan + mount in `api/main.py`**

Replace the app-construction block (keep everything else — routers, `/`, `/me` — unchanged):

```python
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

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
app.mount("/mcp", mcp_server.mcp_asgi_app)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: PASS (4 tests)

Run: `python -m pytest -q`
Expected: full suite PASS. Watch specifically for pre-existing tests breaking on the new lifespan — if any test constructs `TestClient(app)` without a `with` block it won't run the lifespan and `/mcp` returns 503 there, which is fine; nothing else changes behavior.

- [ ] **Step 6: Commit**

```bash
git add api/mcp_server.py api/main.py requirements.txt tests/test_mcp_server.py
git commit -m "Mount token-authenticated MCP server at /mcp (6 wishlist tools)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Dashboard "Connect Claude (MCP)" token management UI

**Files:**
- Modify: `api/routers/web.py` (dashboard context + 2 routes)
- Modify: `api/templates/dashboard.html` (new card before `{% endblock %}`)
- Create: `api/templates/_tokens.html`
- Test: `tests/test_web_tokens.py`

**Interfaces:**
- Consumes: `core.tokens.generate_token`, `core.models.ApiToken`, existing `require_web_user`, `templates`.
- Produces: `POST /app/tokens` (form field `name`) → `_tokens.html` fragment including the one-time plaintext; `DELETE /app/tokens/{token_id}` → `_tokens.html` fragment. Dashboard context gains `tokens: list[ApiToken]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_tokens.py`:

```python
"""Tests for the dashboard API-token management UI (Phase 8)."""

from core.tokens import TOKEN_PREFIX


def _register(client, email="tok@example.com", password="supersecret"):
    return client.post(
        "/register", data={"email": email, "password": password},
        follow_redirects=False,
    )


def test_create_token_shows_plaintext_once(client):
    _register(client)
    resp = client.post("/app/tokens", data={"name": "my-laptop"})
    assert resp.status_code == 200
    assert TOKEN_PREFIX in resp.text          # one-time reveal in the fragment
    assert "my-laptop" in resp.text

    # the dashboard lists the token by name but never its value
    dash = client.get("/app")
    assert "my-laptop" in dash.text
    assert TOKEN_PREFIX not in dash.text


def test_revoke_token(client):
    _register(client)
    client.post("/app/tokens", data={"name": "old-token"})

    # fish the token id out of the re-rendered fragment on the dashboard
    dash = client.get("/app")
    marker = 'hx-delete="/app/tokens/'
    start = dash.text.index(marker) + len(marker)
    token_id = dash.text[start : dash.text.index('"', start)]

    resp = client.delete(f"/app/tokens/{token_id}")
    assert resp.status_code == 200
    assert "old-token" not in client.get("/app").text


def test_tokens_are_user_scoped(client):
    _register(client, email="owner@example.com")
    client.post("/app/tokens", data={"name": "owners-token"})
    dash = client.get("/app")
    marker = 'hx-delete="/app/tokens/'
    start = dash.text.index(marker) + len(marker)
    token_id = dash.text[start : dash.text.index('"', start)]

    client.cookies.clear()
    _register(client, email="intruder@example.com")
    client.delete(f"/app/tokens/{token_id}")  # must be a no-op

    client.cookies.clear()
    client.post(
        "/login", data={"email": "owner@example.com", "password": "supersecret"}
    )
    assert "owners-token" in client.get("/app").text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_tokens.py -v`
Expected: FAIL — `POST /app/tokens` returns 404/405 (route doesn't exist)

- [ ] **Step 3: Add routes to `api/routers/web.py`**

Extend the existing imports:

```python
from core.models import Alert, ApiToken, DeadLetter, Offer, PricePoint, TrackedProduct, User
from core.tokens import generate_token
```

Update `dashboard()` to include tokens in the context:

```python
@router.get("/app", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    items = list(
        db.scalars(
            select(TrackedProduct)
            .where(TrackedProduct.user_id == user.id)
            .order_by(TrackedProduct.created_at.desc())
        )
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "items": items, "tokens": _user_tokens(db, user)},
    )
```

Add the helper + routes (new section at the end of the file):

```python
# --- API tokens for the MCP endpoint (Phase 8) ------------------------------
def _user_tokens(db: Session, user: User) -> list[ApiToken]:
    return list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == user.id)
            .order_by(ApiToken.created_at.desc())
        )
    )


def _render_tokens(
    request: Request, user: User, db: Session, new_token: str | None = None
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_tokens.html",
        {"tokens": _user_tokens(db, user), "new_token": new_token},
    )


@router.post("/app/tokens", response_class=HTMLResponse)
def create_api_token(
    request: Request,
    name: str = Form(...),
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    plain, digest = generate_token()
    db.add(ApiToken(user_id=user.id, name=name.strip() or "unnamed", token_hash=digest))
    db.commit()
    # `plain` rides only in this one response — it is never persisted.
    return _render_tokens(request, user, db, new_token=plain)


@router.delete("/app/tokens/{token_id}", response_class=HTMLResponse)
def revoke_api_token(
    request: Request,
    token_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return _render_tokens(request, user, db)
```

- [ ] **Step 4: Create `api/templates/_tokens.html`**

```html
{% if new_token %}
<div class="card" style="margin-bottom: 12px;">
  <p class="hint" style="margin: 0 0 6px;">Copy your token now — it won't be shown again.</p>
  <code style="user-select: all; word-break: break-all;">{{ new_token }}</code>
</div>
{% endif %}

{% if tokens %}
<table>
  <thead>
    <tr><th>Name</th><th>Created</th><th>Last used</th><th></th></tr>
  </thead>
  <tbody>
    {% for t in tokens %}
    <tr>
      <td>{{ t.name }}</td>
      <td class="muted">{{ t.created_at.strftime("%Y-%m-%d") }}</td>
      <td class="muted">{{ t.last_used_at.strftime("%Y-%m-%d %H:%M") if t.last_used_at else "never" }}</td>
      <td>
        <button class="secondary" hx-delete="/app/tokens/{{ t.id }}"
                hx-target="#tokens" hx-swap="innerHTML"
                hx-confirm="Revoke '{{ t.name }}'? Clients using it will stop working.">
          Revoke
        </button>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p class="muted">No tokens yet. Create one to connect Claude to your wishlist.</p>
{% endif %}
```

(Match table styling to whatever `ops_dashboard.html` uses for its tables — reuse, don't invent.)

- [ ] **Step 5: Add the card to `api/templates/dashboard.html`**

Insert immediately before `{% endblock %}`:

```html
<div class="card">
  <h2>Connect Claude (MCP)</h2>
  <p class="hint">Create an API token, then add this app as an MCP server — Claude can
    search deals, track products, and set alerts on your behalf:</p>
  <pre style="overflow-x:auto;"><code>claude mcp add --transport http deal-hunter {{ request.base_url }}mcp \
  --header "Authorization: Bearer &lt;your token&gt;"</code></pre>
  <form hx-post="/app/tokens" hx-target="#tokens" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.reset()">
    <div class="row" style="align-items:flex-end;">
      <div class="grow">
        <label for="token-name">Token name</label>
        <input id="token-name" name="name" placeholder="my-laptop" required>
      </div>
      <div><button type="submit">Create token</button></div>
    </div>
  </form>
  <div id="tokens" style="margin-top:14px;">
    {% include "_tokens.html" %}
  </div>
</div>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_tokens.py tests/test_web.py -v`
Expected: PASS (new tests + no regressions in existing web tests)

- [ ] **Step 7: Commit**

```bash
git add api/routers/web.py api/templates/_tokens.html api/templates/dashboard.html tests/test_web_tokens.py
git commit -m "Add dashboard token management UI for the MCP endpoint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Phase A docs + full verification

**Files:**
- Modify: `README.md` (build-phases list + a short "MCP" section)

- [ ] **Step 1: Update README**

In the "Build phases" list, append:

```markdown
- **Phase 8 — Remote MCP server** ✅ token-authenticated MCP endpoint at `/mcp` (Streamable HTTP); create a token on the dashboard, then let Claude manage your wishlist
```

After the "Running locally" section, add:

````markdown
## Connecting Claude (MCP)

Create an API token in the dashboard (`/app` → "Connect Claude"), then:

```bash
claude mcp add --transport http deal-hunter https://<your-host>/mcp \
  --header "Authorization: Bearer dh_live_..."
```

Six tools are exposed, all scoped to the token's user: `list_wishlist`,
`add_tracked_product`, `get_best_deal`, `get_price_history`, `search_deals`,
`create_alert`. Tokens are stored as SHA-256 digests and shown exactly once.
````

- [ ] **Step 2: Full-suite verification**

Run: `python -m pytest -q`
Expected: PASS, zero failures.

Run: `docker compose up --build -d && sleep 20 && curl -s -o /dev/null -w "%{http_code}" -X POST localhost:8000/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","method":"ping","id":1}'`
Expected: `401` (auth gate live behind real uvicorn; also confirms the Alembic migration applies cleanly). Then `docker compose down`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document Phase 8: remote MCP server

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

**PAUSE — Phase A checkpoint.** Phase A is independently deployable and demo-able. Confirm with the user before starting Phase B (per their phase-by-phase preference).

---

# Phase B — SSE live prices

### Task 7: Price-update events (`core/events.py` + worker publish)

**Files:**
- Create: `core/events.py`
- Modify: `workers/tasks.py` (one import + one call in `fetch_offer`)
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: `core.cache.get_redis()`.
- Produces:
  - `core.events.price_update_channel(tracked_product_id: str) -> str` — `"price_updates:{id}"`
  - `core.events.publish_price_update(tracked_product_id: str, client=None) -> None` — best-effort, never raises.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_events.py`:

```python
"""Tests for price-update pub/sub events (Phase 9)."""

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import fakeredis
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.events import price_update_channel, publish_price_update
from core.models import Offer, TrackedProduct, User

import core.models  # noqa: F401


def test_publish_reaches_subscriber():
    client = fakeredis.FakeRedis(decode_responses=True)
    sub = client.pubsub(ignore_subscribe_messages=True)
    sub.subscribe(price_update_channel("abc"))

    publish_price_update("abc", client=client)

    msg = sub.get_message(timeout=1)
    assert msg is not None
    assert msg["channel"] == "price_updates:abc"
    assert msg["data"] == "abc"


def test_publish_swallows_redis_errors():
    class ExplodingClient:
        def publish(self, *a, **k):
            raise ConnectionError("redis down")

    publish_price_update("abc", client=ExplodingClient())  # must not raise


# --- call-site test: fetch_offer publishes after recording a price ----------
class FakeSource:
    name = "fake"

    def fetch(self, source_product_id):
        from sources.base import NormalizedOffer

        return NormalizedOffer(
            source=self.name, source_product_id=source_product_id, title="X",
            price=Decimal("90.00"), currency="USD", url="http://x.test/x",
            available=True,
        )


@pytest.fixture
def db_with_offer():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(email="e@example.com", hashed_password="x")
    session.add(user)
    session.flush()
    tp = TrackedProduct(user_id=user.id, title="T", query="t")
    session.add(tp)
    session.flush()
    offer = Offer(
        tracked_product_id=tp.id, source="fake", source_product_id="x1",
        title="T", url="http://x.test/x", currency="USD",
        last_price=None, is_available=True,  # no previous price -> no notify
    )
    session.add(offer)
    session.commit()
    yield session, offer, tp


def test_fetch_offer_publishes_price_update(monkeypatch, db_with_offer):
    session, offer, tp = db_with_offer
    published: list[str] = []

    @contextmanager
    def fake_lock(*args, **kwargs):
        yield True

    monkeypatch.setattr("workers.tasks.SessionLocal", lambda: session)
    monkeypatch.setattr("workers.tasks.offer_lock", fake_lock)
    monkeypatch.setattr(
        "workers.tasks.RateLimiter",
        lambda *a, **k: SimpleNamespace(allow=lambda source: True),
    )
    monkeypatch.setattr("workers.tasks.get_source", lambda name: FakeSource())
    monkeypatch.setattr("workers.tasks.invalidate_best_deal", lambda *a, **k: None)
    monkeypatch.setattr(
        "workers.tasks.publish_price_update", lambda tp_id: published.append(tp_id)
    )

    from workers.tasks import fetch_offer

    result = fetch_offer(str(offer.id))

    assert result == "90.00"
    # publish fires on EVERY recorded price, not just drops — the page should
    # tick live even when the price is flat or rising.
    assert published == [str(tp.id)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.events'`

- [ ] **Step 3: Implement `core/events.py`**

```python
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
```

- [ ] **Step 4: Add the call in `workers/tasks.py`**

Add to the imports block:

```python
from core.events import publish_price_update
```

In `fetch_offer`, immediately after the existing `invalidate_best_deal(tracked_product_id)` line on the success path (right before `metrics.inc("deal_fetch_success_total")`):

```python
        invalidate_best_deal(tracked_product_id)
        publish_price_update(tracked_product_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_events.py -v`
Expected: PASS (3 tests)

Run: `python -m pytest -q`
Expected: full suite PASS.

- [ ] **Step 6: Commit**

```bash
git add core/events.py workers/tasks.py tests/test_events.py
git commit -m "Publish price_updates events from fetch_offer via Redis pub/sub

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: SSE stream endpoint (`GET /app/items/{item_id}/stream`)

**Files:**
- Modify: `api/routers/web.py` (context refactor + seams + endpoint)
- Test: `tests/test_sse.py`

**Interfaces:**
- Consumes: `core.events.price_update_channel` (Task 7), existing `_render_offers`, `_owned`, `api.deps._user_from_cookie`, `templates`.
- Produces:
  - `web._offers_context(item, db) -> dict` (`{"offers", "deal", "spark"}`) — shared by `_render_offers` and the stream.
  - `web._session()` and `web._aioredis()` — monkeypatchable seams.
  - `GET /app/items/{item_id}/stream` → `text/event-stream`; first `offers` event immediately on connect; one per pub/sub message after; `: keep-alive` comment every 15s; 503 if Redis is unreachable at subscribe time; 303→/login unauthenticated; 404 non-owner.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sse.py`:

```python
"""Tests for the SSE live-price stream (Phase 9).

fakeredis's FakeServer is shared between the endpoint's async subscriber and
the test's sync publisher, so a publish from the test surfaces as an SSE
frame — the full worker->browser path minus the real network.
"""

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from core.db import Base, get_db
from core.events import price_update_channel
from core.models import TrackedProduct

import core.models  # noqa: F401


@pytest.fixture
def sse_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("api.routers.web._session", lambda: TestingSessionLocal())

    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        "api.routers.web._aioredis",
        lambda: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )
    publisher = fakeredis.FakeRedis(server=server, decode_responses=True)

    with TestClient(app) as client:
        yield client, TestingSessionLocal, publisher
    app.dependency_overrides.clear()


def _register_and_add_item(client, session_factory, email="sse@example.com"):
    client.post(
        "/register", data={"email": email, "password": "supersecret"},
        follow_redirects=False,
    )
    client.post("/app/items", data={"title": "Camera", "query": "camera"})
    db = session_factory()
    item = db.scalar(select(TrackedProduct).where(TrackedProduct.title == "Camera"))
    item_id = item.id
    db.close()
    return item_id


def _read_until_event(lines, event="offers", max_lines=200):
    """Consume iter_lines() until an `event:` line, then return its data block."""
    for _ in range(max_lines):
        line = next(lines)
        if line.startswith(f"event: {event}"):
            data = []
            for _ in range(max_lines):
                d = next(lines)
                if d == "":  # blank line terminates one SSE frame
                    return "\n".join(data)
                if d.startswith("data: "):
                    data.append(d[len("data: "):])
    raise AssertionError(f"no '{event}' event within {max_lines} lines")


def test_stream_requires_auth(sse_env):
    client, session_factory, _ = sse_env
    item_id = _register_and_add_item(client, session_factory)
    client.cookies.clear()
    resp = client.get(f"/app/items/{item_id}/stream", follow_redirects=False)
    assert resp.status_code == 303


def test_stream_rejects_non_owner(sse_env):
    client, session_factory, _ = sse_env
    item_id = _register_and_add_item(client, session_factory)
    client.cookies.clear()
    client.post(
        "/register", data={"email": "other@example.com", "password": "supersecret"},
        follow_redirects=False,
    )
    resp = client.get(f"/app/items/{item_id}/stream", follow_redirects=False)
    assert resp.status_code == 404


def test_stream_sends_initial_then_published_updates(sse_env):
    client, session_factory, publisher = sse_env
    item_id = _register_and_add_item(client, session_factory)

    with client.stream("GET", f"/app/items/{item_id}/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        lines = resp.iter_lines()
        first = _read_until_event(lines)
        assert "Best deal" in first  # the rendered _offers.html fragment

        # a worker recording a price publishes -> a second frame arrives
        publisher.publish(price_update_channel(str(item_id)), str(item_id))
        second = _read_until_event(lines)
        assert "Best deal" in second
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sse.py -v`
Expected: FAIL — 404 on `/app/items/{id}/stream` (route doesn't exist)

- [ ] **Step 3: Refactor `_render_offers` and add the endpoint in `api/routers/web.py`**

Add imports at the top:

```python
from functools import partial

import anyio.to_thread
import redis.asyncio as aioredis
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from api.deps import SESSION_COOKIE, _user_from_cookie, get_optional_web_user, require_web_user
from core.events import price_update_channel
```

Split `_render_offers` into context-builder + response (replace the existing function):

```python
def _offers_context(item: TrackedProduct, db: Session) -> dict:
    offers = list(
        db.scalars(
            select(Offer)
            .where(Offer.tracked_product_id == item.id)
            .order_by(Offer.last_price.is_(None), Offer.last_price)
        )
    )
    deal = _best_deal(db, item)

    spark = None
    if deal.best_offer_id is not None:
        history = list(
            db.scalars(
                select(PricePoint.price)
                .where(PricePoint.offer_id == deal.best_offer_id)
                .order_by(PricePoint.observed_at.desc())
                .limit(40)
            )
        )
        spark = _sparkline(list(reversed(history)))

    return {"offers": offers, "deal": deal, "spark": spark}


def _render_offers(request: Request, item: TrackedProduct, db: Session) -> HTMLResponse:
    return templates.TemplateResponse(request, "_offers.html", _offers_context(item, db))
```

Add the SSE section at the end of the file:

```python
# --- SSE live prices (Phase 9) ----------------------------------------------
# Both factories exist as seams so tests can inject sqlite + fakeredis.
def _session() -> Session:
    from core.db import SessionLocal

    return SessionLocal()


def _aioredis() -> "aioredis.Redis":
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _sse_frame(event: str, html: str) -> str:
    data = "\n".join(f"data: {line}" for line in html.splitlines()) or "data:"
    return f"event: {event}\n{data}\n\n"


def _render_offers_html(item_id: uuid.UUID) -> str:
    """Render the offers fragment in its own short-lived session.

    The stream outlives any request-scoped session by minutes; holding a
    Depends(get_db) session open that long would pin a pool connection per
    open tab.
    """
    db = _session()
    try:
        item = db.get(TrackedProduct, item_id)
        if item is None:
            return ""
        return templates.get_template("_offers.html").render(_offers_context(item, db))
    finally:
        db.close()


@router.get("/app/items/{item_id}/stream")
async def item_stream(request: Request, item_id: uuid.UUID):
    # Auth + ownership run in a short-lived session (see _render_offers_html
    # for why not Depends(get_db)). Same 303/404 semantics as the page routes.
    def _gate() -> None:
        db = _session()
        try:
            user = _user_from_cookie(request, db)
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_303_SEE_OTHER,
                    headers={"Location": "/login", "HX-Redirect": "/login"},
                )
            _owned(item_id, user, db)
        finally:
            db.close()

    await anyio.to_thread.run_sync(_gate)

    r = _aioredis()
    pubsub = r.pubsub()
    channel = price_update_channel(str(item_id))
    try:
        await pubsub.subscribe(channel)
    except Exception:  # noqa: BLE001 - Redis down: the page still works statically
        await r.aclose()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live updates are unavailable right now",
        )

    async def event_stream():
        try:
            # Immediate first frame: the client renders without a separate
            # hx-get, and reconnects repaint instantly.
            html = await anyio.to_thread.run_sync(partial(_render_offers_html, item_id))
            yield _sse_frame("offers", html)
            while not await request.is_disconnected():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15.0
                )
                if msg is None:
                    yield ": keep-alive\n\n"  # comment frame; proxies stay open
                    continue
                html = await anyio.to_thread.run_sync(
                    partial(_render_offers_html, item_id)
                )
                yield _sse_frame("offers", html)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await r.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tell buffering proxies to pass frames through
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sse.py -v`
Expected: PASS (3 tests)

Run: `python -m pytest -q`
Expected: full suite PASS (the `_render_offers` refactor must not break `test_web.py`).

- [ ] **Step 5: Commit**

```bash
git add api/routers/web.py tests/test_sse.py
git commit -m "Add SSE stream endpoint: offers fragment per price_updates event

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: HTMX SSE frontend + Phase B docs + final verification

**Files:**
- Modify: `api/templates/base.html` (one script tag)
- Modify: `api/templates/item_detail.html` (offers container)
- Modify: `README.md`
- Test: `tests/test_web.py` (append one test)

**Interfaces:**
- Consumes: `GET /app/items/{item_id}/stream` (Task 8), htmx SSE extension (`htmx-ext-sse`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def test_item_detail_uses_sse_stream(client):
    _register(client, email="sse-ui@example.com")
    client.post("/app/items", data={"title": "Camera", "query": "camera"})
    dash = client.get("/app")
    marker = 'href="/app/items/'
    start = dash.text.index(marker) + len('href="')
    item_url = dash.text[start : dash.text.index('"', start)]

    page = client.get(item_url)
    assert 'hx-ext="sse"' in page.text
    assert f'sse-connect="{item_url}/stream"' in page.text
    assert 'sse-swap="offers"' in page.text
    assert "every 5s" not in page.text  # polling is gone
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web.py::test_item_detail_uses_sse_stream -v`
Expected: FAIL (`hx-ext="sse"` not in page)

- [ ] **Step 3: Load the SSE extension in `api/templates/base.html`**

Directly after the existing htmx script tag (`<script src="https://unpkg.com/htmx.org@2.0.3"></script>`), add:

```html
<script src="https://unpkg.com/htmx-ext-sse@2.2.2"></script>
```

- [ ] **Step 4: Swap polling for SSE in `api/templates/item_detail.html`**

Replace the hint line and the `#offers` div (keep the Refresh button as-is — its `hx-target="#offers"` still works):

```html
  <p class="hint"><span class="live-dot" aria-hidden="true"></span>Live — prices stream in the moment a worker records them.</p>
  <div hx-ext="sse" sse-connect="/app/items/{{ item.id }}/stream">
    <div id="offers" sse-swap="offers" style="margin-top:14px;">
      <div aria-hidden="true">
        <div class="skel skel-verdict"></div>
        <div class="skel skel-row"></div>
        <div class="skel skel-row"></div>
        <div class="skel skel-row"></div>
      </div>
      <span class="muted" style="position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);">Loading offers…</span>
    </div>
  </div>
```

(The `hx-get`/`hx-trigger="load, every 5s"` attributes are removed: the stream's immediate first frame replaces the load-trigger, and pub/sub replaces the poll. The skeleton shows only until that first frame arrives. The base.html swap-choreography rule that suppresses entrance replays keys off swaps into `#offers`, which is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_web.py tests/test_sse.py -v`
Expected: PASS

- [ ] **Step 6: Update README**

Build-phases list, append:

```markdown
- **Phase 9 — SSE live prices** ✅ item page streams price updates over Server-Sent Events (Redis pub/sub → `/app/items/{id}/stream` → htmx SSE extension) instead of polling
```

- [ ] **Step 7: Full verification, including the live loop**

Run: `python -m pytest -q`
Expected: full suite PASS.

Run: `docker compose up --build -d && sleep 25`, then open `http://localhost:8000/app`, register, add an item, open its detail page, and confirm the offers panel populates and then updates on its own within one refresh cycle (~30s) **without** any 5-second polling in the browser dev-tools network tab (one persistent `stream` request instead). Then `docker compose down`.

- [ ] **Step 8: Commit**

```bash
git add api/templates/base.html api/templates/item_detail.html tests/test_web.py README.md
git commit -m "Stream live prices over SSE on the item page (replaces polling)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

**DONE — Phase B checkpoint.** Both features complete. Next steps outside this plan: push branch, open PR, deploy, and run the two manual live smoke tests from the spec (connect `claude mcp add` against prod; watch two browser windows tick).
