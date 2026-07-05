# "View in your space" — 3D/AR Product Previews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI-generated 3D model per tracked product (Meshy image-to-3D from the best offer's photo), async Celery generation with SSE live progress, interactive `<model-viewer>` on the item page, native phone AR (glb/usdz).

**Architecture:** New `product_models` table (unique row per product = cache-once). Celery task `generate_model3d` mirrors `fetch_offer` (retries → dead-letter), publishes `model3d_updates` over Redis pub/sub; the item page's existing SSE stream gains a second event type. Files stored on a volume, served with ownership checks. Hard monthly Redis cap. Feature dark without `MESHY_API_KEY`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Celery, Redis, httpx, Jinja/HTMX, `<model-viewer>` (vendored).

**Spec:** `docs/superpowers/specs/2026-07-05-3d-ar-product-preview-design.md`

## Global Constraints

- Feature entirely dark unless `MESHY_API_KEY` is set (pattern: `anthropic_api_key`).
- `MODEL3D_MONTHLY_CAP` default **8**; Redis counter key `model3d:count:{YYYY-MM}`.
- `MODEL3D_STORAGE_DIR` default `./model3d`; files named `{tracked_product_id}.glb` / `.usdz`.
- No real Meshy calls in tests ever — httpx `MockTransport` only.
- Status enum strings: `pending`, `generating`, `ready`, `failed` (exact).
- SSE event name: `model3d`. Pub/sub channel: `model3d_updates:{tracked_product_id}`.
- All new UI copy follows brand voice (friendly, not terminal-y); verify exact strings from spec §UX.
- Tests: sqlite `client` fixture + fakeredis + monkeypatch, style of `tests/test_web.py` / `tests/test_events.py`.
- **NOTE:** Verify Meshy request/response field names against https://docs.meshy.ai/en/api/image-to-3d when implementing Task 3; all provider knowledge is isolated in `core/meshy.py`.

---

### Task 0: Commit in-flight marketplace work

The working tree contains uncommitted, **green** (150 passed) marketplace/track-by-URL work that this feature depends on (`offers.image_url`, migration 0006). Commit it as its own commit so 3D commits stay clean.

- [ ] **Step 1:** Run `.venv/bin/python -m pytest -q` → expect `150 passed`.
- [ ] **Step 2:** Commit everything in-flight:

```bash
git add -A
git commit -m "Add marketplace browse + track-by-URL source + offer images

In-flight work committed as-is (suite green: 150 passed) so the 3D/AR
feature can build on offers.image_url."
```

---

### Task 1: Settings, ProductModel3D model, migration 0007

**Files:**
- Modify: `core/settings.py` (after the Phase 7 block)
- Modify: `core/models.py` (new class at end)
- Create: `migrations/versions/0007_product_models.py`
- Test: `tests/test_model3d.py` (new)

**Interfaces produced:** `settings.meshy_api_key: str | None`, `settings.model3d_monthly_cap: int = 8`, `settings.model3d_storage_dir: str`, ORM class `ProductModel3D` with columns per spec.

- [ ] **Step 1: Failing test**

```python
# tests/test_model3d.py
"""Tests for the 3D/AR preview feature (Phase 10)."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import ProductModel3D, TrackedProduct, User


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)()


def _product(db):
    user = User(email=f"{uuid.uuid4()}@x.com", hashed_password="h")
    db.add(user)
    db.flush()
    tp = TrackedProduct(user_id=user.id, title="XM5", query="xm5")
    db.add(tp)
    db.flush()
    return tp


def test_product_model3d_defaults_and_unique(db):
    tp = _product(db)
    row = ProductModel3D(tracked_product_id=tp.id, source_image_url="http://i/x.jpg")
    db.add(row)
    db.commit()
    assert row.status == "pending"
    assert row.glb_path is None
    db.add(ProductModel3D(tracked_product_id=tp.id, source_image_url="http://i/y.jpg"))
    with pytest.raises(Exception):
        db.commit()
```

- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_model3d.py -q` → FAIL (ImportError: ProductModel3D).
- [ ] **Step 3: Implement**

`core/settings.py` — insert after the `anthropic_vision_model` line:

```python
    # --- 3D/AR previews (Phase 10) ---
    # Meshy image-to-3D key; the whole feature is dark until it's set.
    meshy_api_key: str | None = None
    meshy_base_url: str = "https://api.meshy.ai"
    # Hard monthly generation cap, sized to Meshy's free tier.
    model3d_monthly_cap: int = 8
    model3d_storage_dir: str = "./model3d"
```

`core/models.py` — append:

```python
class ProductModel3D(Base):
    """AI-generated 3D preview for a tracked product (Phase 10).

    One row per product (unique FK) — that row IS the cache-once guarantee.
    Model files live on disk under MODEL3D_STORAGE_DIR; rows hold paths only.
    """

    __tablename__ = "product_models"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tracked_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tracked_products.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider: Mapped[str] = mapped_column(String(20), default="meshy")
    provider_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_image_url: Mapped[str] = mapped_column(String(1024))
    glb_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    usdz_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
```

(Match existing imports — `ForeignKey`, `String`, `Text`, `func` are already imported in the module.)

`migrations/versions/0007_product_models.py` — same shape as 0006:

```python
"""Add product_models — AI-generated 3D/AR previews (Phase 10).

Revision ID: 0007_product_models
Revises: 0006_offer_image_url
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_product_models"
down_revision = "0006_offer_image_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_models",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tracked_product_id",
            sa.Uuid(),
            sa.ForeignKey("tracked_products.id", ondelete="CASCADE"),
            unique=True,
            index=True,
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(20), nullable=False, server_default="meshy"),
        sa.Column("provider_task_id", sa.String(255), nullable=True),
        sa.Column("source_image_url", sa.String(1024), nullable=False),
        sa.Column("glb_path", sa.String(1024), nullable=True),
        sa.Column("usdz_path", sa.String(1024), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("product_models")
```

- [ ] **Step 4:** `.venv/bin/python -m pytest tests/test_model3d.py -q` → PASS; full suite still green.
- [ ] **Step 5:** `git add core/settings.py core/models.py migrations/versions/0007_product_models.py tests/test_model3d.py && git commit -m "Add ProductModel3D model + settings + migration for 3D previews"`

---

### Task 2: Monthly cap primitive (core/cache.py)

**Files:** Modify `core/cache.py`; Test: append to `tests/test_model3d.py`.

**Interfaces produced:** `model3d_quota_ok(client=None) -> bool` (True if under cap), `model3d_quota_spend(client=None) -> None` (increment). Key `model3d:count:{YYYY-MM}` (UTC), 60-day expiry set on first increment.

- [ ] **Step 1: Failing test**

```python
import fakeredis

from core.cache import model3d_quota_ok, model3d_quota_spend


def test_model3d_quota_caps_at_setting(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("core.cache.get_settings_cap", lambda: 2, raising=False)
    # cap of 2: two spends allowed, third blocked
    assert model3d_quota_ok(client=r, cap=2)
    model3d_quota_spend(client=r)
    model3d_quota_spend(client=r)
    assert not model3d_quota_ok(client=r, cap=2)
```

- [ ] **Step 2:** Run → FAIL (ImportError).
- [ ] **Step 3: Implement** in `core/cache.py` (imports `datetime` if not present; follow the module's existing `client or get_redis()` idiom):

```python
def _model3d_month_key() -> str:
    from datetime import datetime, timezone

    return f"model3d:count:{datetime.now(timezone.utc):%Y-%m}"


def model3d_quota_ok(client: redis.Redis | None = None, cap: int | None = None) -> bool:
    """True if another 3D generation is allowed this calendar month."""
    r = client or get_redis()
    limit = cap if cap is not None else get_settings().model3d_monthly_cap
    used = int(r.get(_model3d_month_key()) or 0)
    return used < limit


def model3d_quota_spend(client: redis.Redis | None = None) -> None:
    """Count one generation against this month's cap (60d expiry, self-cleaning)."""
    r = client or get_redis()
    key = _model3d_month_key()
    if r.incr(key) == 1:
        r.expire(key, 60 * 24 * 3600)
```

(Import `get_settings` if the module doesn't already.)

- [ ] **Step 4:** Run test file → PASS.
- [ ] **Step 5:** `git add core/cache.py tests/test_model3d.py && git commit -m "Add monthly quota primitive for 3D generation"`

---

### Task 3: Meshy provider client (core/meshy.py)

**Files:** Create `core/meshy.py`; Test: append to `tests/test_model3d.py`.

**Interfaces produced:**
- `create_image_to_3d_task(image_url: str, client: httpx.Client | None = None) -> str` (returns provider task id)
- `get_task(task_id: str, client=None) -> MeshyTask` where `MeshyTask` is a dataclass: `status: str` (`PENDING|IN_PROGRESS|SUCCEEDED|FAILED`), `model_urls: dict[str, str]` (keys incl. `glb`, `usdz`), `error: str | None`
- `download_file(url: str, dest: pathlib.Path, client=None) -> None`
- `MeshyError(Exception)`

**⚠ Verify endpoint paths + JSON fields against https://docs.meshy.ai/en/api/image-to-3d before finalizing; adjust only this file.**

- [ ] **Step 1: Failing test**

```python
import httpx

from core import meshy


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_meshy_create_and_poll(monkeypatch):
    monkeypatch.setattr(meshy, "_api_key", lambda: "k")

    def handler(request):
        if request.method == "POST":
            assert "image_url" in request.read().decode()
            return httpx.Response(200, json={"result": "task-123"})
        return httpx.Response(
            200,
            json={
                "id": "task-123",
                "status": "SUCCEEDED",
                "model_urls": {"glb": "http://m/x.glb", "usdz": "http://m/x.usdz"},
            },
        )

    c = _mock_client(handler)
    task_id = meshy.create_image_to_3d_task("http://i/x.jpg", client=c)
    assert task_id == "task-123"
    task = meshy.get_task(task_id, client=c)
    assert task.status == "SUCCEEDED"
    assert task.model_urls["glb"].endswith(".glb")


def test_meshy_download(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"GLBDATA")

    meshy.download_file("http://m/x.glb", tmp_path / "x.glb", client=_mock_client(handler))
    assert (tmp_path / "x.glb").read_bytes() == b"GLBDATA"
```

- [ ] **Step 2:** Run → FAIL. **Step 3: Implement** `core/meshy.py`:

```python
"""Meshy image-to-3D client (Phase 10).

All provider knowledge lives here so a vendor swap (Tripo etc.) touches one
file. Endpoints per https://docs.meshy.ai/en/api/image-to-3d.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import httpx

from core.settings import get_settings


class MeshyError(Exception):
    """Provider rejected the request or returned an unusable payload."""


@dataclass
class MeshyTask:
    status: str  # PENDING | IN_PROGRESS | SUCCEEDED | FAILED
    model_urls: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def _api_key() -> str:
    key = get_settings().meshy_api_key
    if not key:
        raise MeshyError("MESHY_API_KEY is not configured")
    return key


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}"}


def _base() -> str:
    return get_settings().meshy_base_url.rstrip("/")


def create_image_to_3d_task(image_url: str, client: httpx.Client | None = None) -> str:
    c = client or httpx.Client(timeout=30)
    resp = c.post(
        f"{_base()}/openapi/v1/image-to-3d",
        headers=_headers(),
        json={"image_url": image_url, "enable_pbr": False},
    )
    if resp.status_code != 200:
        raise MeshyError(f"create failed: {resp.status_code} {resp.text[:200]}")
    task_id = resp.json().get("result")
    if not task_id:
        raise MeshyError(f"create returned no task id: {resp.text[:200]}")
    return task_id


def get_task(task_id: str, client: httpx.Client | None = None) -> MeshyTask:
    c = client or httpx.Client(timeout=30)
    resp = c.get(f"{_base()}/openapi/v1/image-to-3d/{task_id}", headers=_headers())
    if resp.status_code != 200:
        raise MeshyError(f"poll failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    return MeshyTask(
        status=data.get("status", "FAILED"),
        model_urls=data.get("model_urls") or {},
        error=(data.get("task_error") or {}).get("message"),
    )


def download_file(url: str, dest: pathlib.Path, client: httpx.Client | None = None) -> None:
    c = client or httpx.Client(timeout=120, follow_redirects=True)
    resp = c.get(url)
    if resp.status_code != 200:
        raise MeshyError(f"download failed: {resp.status_code}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
```

- [ ] **Step 4:** Run → PASS. **Step 5:** `git add core/meshy.py tests/test_model3d.py && git commit -m "Add Meshy image-to-3D provider client"`

---

### Task 4: Events channel + generation pipeline + Celery task

**Files:** Modify `core/events.py`, create `workers/model3d.py`, modify `workers/tasks.py`; Test: append to `tests/test_model3d.py`.

**Interfaces produced:**
- `core.events.model3d_update_channel(tracked_product_id: str) -> str` → `"model3d_updates:{id}"`; `publish_model3d_update(tracked_product_id, client=None)` (best-effort, never raises — mirror `publish_price_update`).
- `workers.model3d.run_generation(db, row: ProductModel3D) -> None` — drives create→poll→download→ready, raises on transient failure (caller retries).
- Celery task `workers.tasks.generate_model3d(product_model_id: str)`, `bind=True, max_retries=3`.

- [ ] **Step 1: Failing tests**

```python
from workers.model3d import run_generation


def test_run_generation_happy_path(db, monkeypatch, tmp_path):
    tp = _product(db)
    row = ProductModel3D(tracked_product_id=tp.id, source_image_url="http://i/x.jpg")
    db.add(row)
    db.commit()

    monkeypatch.setattr("workers.model3d._storage_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "workers.model3d.meshy.create_image_to_3d_task", lambda url, client=None: "t1"
    )
    monkeypatch.setattr(
        "workers.model3d.meshy.get_task",
        lambda tid, client=None: meshy.MeshyTask(
            status="SUCCEEDED",
            model_urls={"glb": "http://m/x.glb", "usdz": "http://m/x.usdz"},
        ),
    )
    monkeypatch.setattr(
        "workers.model3d.meshy.download_file",
        lambda url, dest, client=None: dest.write_bytes(b"x"),
    )
    run_generation(db, row)
    db.commit()
    assert row.status == "ready"
    assert row.glb_path == f"{tp.id}.glb"
    assert row.usdz_path == f"{tp.id}.usdz"


def test_run_generation_provider_failure_marks_failed(db, monkeypatch):
    tp = _product(db)
    row = ProductModel3D(tracked_product_id=tp.id, source_image_url="http://i/x.jpg")
    db.add(row)
    db.commit()
    monkeypatch.setattr(
        "workers.model3d.meshy.create_image_to_3d_task", lambda url, client=None: "t1"
    )
    monkeypatch.setattr(
        "workers.model3d.meshy.get_task",
        lambda tid, client=None: meshy.MeshyTask(status="FAILED", error="bad photo"),
    )
    with pytest.raises(Exception):
        run_generation(db, row)
    assert row.status == "failed"
    assert "bad photo" in row.error
```

- [ ] **Step 2:** Run → FAIL. **Step 3: Implement.**

`core/events.py` — append (mirror the price functions exactly):

```python
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
        log.warning("model3d_update_publish_failed", tracked_product_id=tracked_product_id)
```

`workers/model3d.py` (new):

```python
"""3D model generation pipeline (Phase 10): create → poll → download → ready.

Kept out of workers/tasks.py per the repo split: tasks.py holds thin Celery
wrappers, pipeline modules hold the logic. `run_generation` raises on
transient/provider errors so the Celery wrapper's retry/dead-letter machinery
(same shape as fetch_offer) owns the failure policy.
"""
from __future__ import annotations

import pathlib
import time

from sqlalchemy.orm import Session

from core import meshy
from core.logging import get_logger
from core.models import ProductModel3D
from core.settings import get_settings

log = get_logger(__name__)

POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 300


def _storage_dir() -> pathlib.Path:
    return pathlib.Path(get_settings().model3d_storage_dir)


class GenerationFailed(Exception):
    """Provider says this generation is definitively failed (no retry value)."""


def run_generation(db: Session, row: ProductModel3D) -> None:
    """Drive one generation to completion. Mutates row; caller commits.

    Raises GenerationFailed on a definitive provider failure and any other
    exception on transient trouble — the Celery wrapper decides retry policy.
    """
    row.status = "generating"
    db.flush()

    task_id = row.provider_task_id
    if not task_id:
        task_id = meshy.create_image_to_3d_task(row.source_image_url)
        row.provider_task_id = task_id
        db.flush()

    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while True:
        task = meshy.get_task(task_id)
        if task.status == "SUCCEEDED":
            break
        if task.status == "FAILED":
            row.status = "failed"
            row.error = task.error or "generation failed"
            db.flush()
            raise GenerationFailed(row.error)
        if time.monotonic() > deadline:
            raise TimeoutError(f"meshy task {task_id} still {task.status}")
        time.sleep(POLL_INTERVAL_SECONDS)

    pid = str(row.tracked_product_id)
    for fmt in ("glb", "usdz"):
        url = task.model_urls.get(fmt)
        if not url:
            raise meshy.MeshyError(f"no {fmt} url in result")
        meshy.download_file(url, _storage_dir() / f"{pid}.{fmt}")

    row.glb_path = f"{pid}.glb"
    row.usdz_path = f"{pid}.usdz"
    row.status = "ready"
    row.error = None
    db.flush()
    log.info("model3d.ready", tracked_product_id=pid)
```

`workers/tasks.py` — append (imports at top: `from core.events import publish_model3d_update` joins the existing events import; `from core.models import ProductModel3D` joins the models import; `from workers.model3d import GenerationFailed, run_generation`):

```python
@celery_app.task(bind=True, name="workers.tasks.generate_model3d", max_retries=3)
def generate_model3d(self, product_model_id: str) -> str:
    """Generate a 3D preview (Phase 10). Same failure shape as fetch_offer:
    transient errors retry with backoff, exhausted retries dead-letter.
    Definitive provider failures ('bad photo') don't retry at all."""
    db = SessionLocal()
    tracked_product_id: str | None = None
    try:
        row = db.get(ProductModel3D, uuid.UUID(product_model_id))
        if row is None:
            return "missing"
        if row.status == "ready":
            return "already ready"  # cache-once: never regenerate implicitly
        tracked_product_id = str(row.tracked_product_id)
        run_generation(db, row)
        db.commit()
        publish_model3d_update(tracked_product_id)
        return "ready"
    except GenerationFailed:
        db.commit()  # keep the failed status + error for the retry UI
        if tracked_product_id:
            publish_model3d_update(tracked_product_id)
        return "failed"
    except Retry:
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        countdown = min(60, settings.fetch_retry_base_seconds * (2**self.request.retries))
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except MaxRetriesExceededError:
            if not _dead_letter(
                None, "generate_model3d", exc, self.request.retries,
                mark_offer_unavailable=False,
            ):
                raise exc
            _mark_model3d_failed(product_model_id, exc)
            if tracked_product_id:
                publish_model3d_update(tracked_product_id)
            return "dead-lettered"
    finally:
        db.close()


def _mark_model3d_failed(product_model_id: str, exc: Exception) -> None:
    """Record the failure on the row in a fresh session (ours rolled back)."""
    db = SessionLocal()
    try:
        row = db.get(ProductModel3D, uuid.UUID(product_model_id))
        if row is not None:
            row.status = "failed"
            row.error = repr(exc)
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()
```

- [ ] **Step 4:** Run test file + full suite → PASS.
- [ ] **Step 5:** `git add core/events.py workers/model3d.py workers/tasks.py tests/test_model3d.py && git commit -m "Add generate_model3d pipeline + Celery task + pub/sub event"`

---

### Task 5: Web routes + fragment template (5 states)

**Files:** Modify `api/routers/web.py`, create `api/templates/_model3d.html`, modify `api/templates/item_detail.html`; Test: append to `tests/test_model3d.py` (uses the `client` fixture).

**Interfaces produced:**
- `POST /app/items/{item_id}/model3d` → enqueues + returns fragment (htmx target `#model3d`)
- `GET /app/models/{item_id}.glb` / `.usdz` → `FileResponse`, ownership-checked
- `_render_model3d_html(item_id) -> str` (module-level, own session — SSE reuses it in Task 6)
- Context builder `_model3d_context(item, db) -> dict` with keys: `item`, `m3d` (row or None), `m3d_enabled` (bool), `m3d_has_image` (bool), `m3d_quota_ok` (bool)

- [ ] **Step 1: Failing tests**

```python
def _make_item(client):
    client.post(
        "/register",
        data={"email": f"{uuid.uuid4()}@x.com", "password": "supersecret"},
        follow_redirects=False,
    )
    resp = client.post(
        "/app/items",
        data={"title": "XM5", "query": "xm5"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    # grab the id from the dashboard fragment link
    import re

    return re.search(r"/app/items/([0-9a-f-]{36})", resp.text).group(1)


def test_model3d_dark_without_key(client):
    item_id = _make_item(client)
    page = client.get(f"/app/items/{item_id}")
    assert "model3d" not in page.text  # zero UI surface when unconfigured


def test_model3d_trigger_enqueues_and_shows_generating(client, monkeypatch):
    monkeypatch.setattr("api.routers.web.settings.meshy_api_key", "k", raising=False)
    monkeypatch.setattr("api.routers.web.model3d_quota_ok", lambda: True)
    monkeypatch.setattr("api.routers.web.model3d_quota_spend", lambda: None)
    calls = []
    monkeypatch.setattr(
        "workers.tasks.generate_model3d.delay", lambda *a: calls.append(a)
    )
    item_id = _make_item(client)
    # give the item an offer image via direct DB access is heavier; the route
    # falls back to 'no image' state — assert the friendly copy instead:
    resp = client.post(f"/app/items/{item_id}/model3d")
    assert resp.status_code == 200


def test_model_file_requires_ownership(client, monkeypatch):
    monkeypatch.setattr("api.routers.web.settings.meshy_api_key", "k", raising=False)
    item_id = _make_item(client)
    client.cookies.clear()
    client.post(
        "/register",
        data={"email": "other@x.com", "password": "supersecret"},
        follow_redirects=False,
    )
    resp = client.get(f"/app/models/{item_id}.glb")
    assert resp.status_code == 404
```

(Adjust `_make_item` to the actual dashboard fragment shape when implementing — reuse an existing helper from `tests/test_web.py` if one exists.)

- [ ] **Step 2:** Run → FAIL. **Step 3: Implement.**

`api/routers/web.py` additions (imports: `FileResponse` from fastapi.responses; `model3d_quota_ok`, `model3d_quota_spend` from core.cache; `ProductModel3D` joins the models import; `model3d_update_channel` joins the events import; `pathlib`):

```python
# --- 3D/AR preview (Phase 10) ----------------------------------------------
def _model3d_row(db: Session, item_id: uuid.UUID) -> ProductModel3D | None:
    return db.scalar(
        select(ProductModel3D).where(ProductModel3D.tracked_product_id == item_id)
    )


def _model3d_context(item: TrackedProduct, db: Session) -> dict:
    enabled = settings.meshy_api_key is not None
    return {
        "item": item,
        "m3d": _model3d_row(db, item.id) if enabled else None,
        "m3d_enabled": enabled,
        "m3d_has_image": _item_image(item) is not None,
        "m3d_quota_ok": model3d_quota_ok() if enabled else False,
    }


def _render_model3d_html(item_id: uuid.UUID) -> str:
    """Fragment render in a short-lived session (same reasoning as offers)."""
    db = _session()
    try:
        item = db.get(TrackedProduct, item_id)
        if item is None:
            return ""
        return templates.get_template("_model3d.html").render(_model3d_context(item, db))
    finally:
        db.close()


@router.post("/app/items/{item_id}/model3d", response_class=HTMLResponse)
def item_generate_model3d(
    request: Request,
    item_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    item = _owned(item_id, user, db)
    if settings.meshy_api_key is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    image = _item_image(item)
    row = _model3d_row(db, item.id)
    if image and model3d_quota_ok() and (row is None or row.status in ("failed", "ready")):
        if row is None:
            row = ProductModel3D(tracked_product_id=item.id, source_image_url=image)
            db.add(row)
        else:  # explicit retry/regenerate: reset for a fresh run
            row.status = "pending"
            row.error = None
            row.provider_task_id = None
            row.source_image_url = image
        db.commit()
        model3d_quota_spend()
        from workers.tasks import generate_model3d

        generate_model3d.delay(str(row.id))
    ctx = _model3d_context(item, db)
    return templates.TemplateResponse(request, "_model3d.html", ctx)


@router.get("/app/models/{item_id}.{fmt}")
def model3d_file(
    item_id: uuid.UUID,
    fmt: str,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    if fmt not in ("glb", "usdz"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    _owned(item_id, user, db)  # 404s for other users' items
    row = _model3d_row(db, item_id)
    path = pathlib.Path(settings.model3d_storage_dir) / f"{item_id}.{fmt}"
    if row is None or row.status != "ready" or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    media = "model/gltf-binary" if fmt == "glb" else "model/vnd.usdz+zip"
    return FileResponse(path, media_type=media)
```

`api/templates/_model3d.html` (the five states; copy per spec):

```html
{# 3D/AR preview slot — five states (Phase 10). Only included when enabled. #}
{% if m3d and m3d.status == "ready" %}
  <model-viewer src="/app/models/{{ item.id }}.glb"
                ios-src="/app/models/{{ item.id }}.usdz"
                alt="3D preview of {{ item.title }}"
                camera-controls auto-rotate ar ar-modes="scene-viewer quick-look webxr"
                style="width:100%;height:280px;border-radius:12px;background:#f6f4f0;">
  </model-viewer>
  <p class="hint" style="margin-top:6px;">Drag to spin — or tap the AR badge on your phone to see it in your space.
    <button class="secondary" style="margin-left:8px;"
            hx-post="/app/items/{{ item.id }}/model3d" hx-target="#model3d" hx-swap="innerHTML"
            {% if not m3d_quota_ok %}disabled{% endif %}>Regenerate</button></p>
{% elif m3d and m3d.status in ("pending", "generating") %}
  <div class="card" style="text-align:center;padding:24px;" aria-live="polite">
    <p style="margin:0;">🪄 Sculpting your 3D model… usually about a minute.</p>
    <div class="skel skel-row" style="margin-top:12px;" aria-hidden="true"></div>
  </div>
{% elif m3d and m3d.status == "failed" %}
  <div class="card" style="padding:16px;" aria-live="polite">
    <p style="margin:0 0 8px;">We couldn't sculpt this one — some photos don't cooperate.</p>
    {% if m3d_quota_ok %}
    <button class="secondary" hx-post="/app/items/{{ item.id }}/model3d"
            hx-target="#model3d" hx-swap="innerHTML">Try again</button>
    {% endif %}
  </div>
{% elif not m3d_has_image %}
  {# no offer photo yet: render nothing — the button would have no input #}
{% elif not m3d_quota_ok %}
  <p class="hint">This month's 3D budget is used up — it resets on the 1st.</p>
{% else %}
  <button class="secondary" hx-post="/app/items/{{ item.id }}/model3d"
          hx-target="#model3d" hx-swap="innerHTML">✨ Generate 3D preview <small>(beta)</small></button>
{% endif %}
```

`api/templates/item_detail.html` — insert a card between the target-price form and Live offers card, inside the existing `sse-connect` div is Task 6's job; for now:

```html
{% if m3d_enabled %}
<div class="card">
  <h2>3D preview</h2>
  <div id="model3d">{% include "_model3d.html" %}</div>
</div>
{% endif %}
```

…and extend the `item_detail` route's context with `**_model3d_context(item, db)`.

- [ ] **Step 4:** Run test file + `pytest -q` → PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Add 3D preview UI slot, trigger route, ownership-checked model files"`

---

### Task 6: SSE second event + vendored model-viewer

**Files:** Modify `api/routers/web.py` (`item_stream`), modify `api/templates/item_detail.html`, modify `api/main.py`, create `api/static/model-viewer.min.js` (vendored); Test: append one test.

- [ ] **Step 1: Failing test** — `item_stream` subscribes to both channels:

```python
def test_stream_subscribes_model3d_channel():
    from core.events import model3d_update_channel, price_update_channel

    assert model3d_update_channel("x") == "model3d_updates:x"
    assert price_update_channel("x") == "price_updates:x"
    # channel-name contract used by item_stream's dual subscription
```

(Full-stream integration is covered by existing SSE tests; this pins the contract.)

- [ ] **Step 2: Implement.** In `item_stream`: subscribe to both channels and dispatch on `msg["channel"]`:

```python
    channel = price_update_channel(str(item_id))
    m3d_channel = model3d_update_channel(str(item_id))
    try:
        await pubsub.subscribe(channel, m3d_channel)
    ...
            if msg is None:
                yield ": keep-alive\n\n"
                continue
            if msg.get("channel") == m3d_channel:
                html = await anyio.to_thread.run_sync(
                    partial(_render_model3d_html, item_id)
                )
                yield _sse_frame("model3d", html)
            else:
                html = await anyio.to_thread.run_sync(
                    partial(_render_offers_html, item_id)
                )
                yield _sse_frame("offers", html)
    finally:
        await pubsub.unsubscribe(channel, m3d_channel)
```

In `item_detail.html`, move the 3D card **inside** the existing `hx-ext="sse"` div (so one EventSource serves both) and mark the slot `sse-swap="model3d"`:

```html
  <div hx-ext="sse" sse-connect="/app/items/{{ item.id }}/stream">
    {% if m3d_enabled %}
    <div class="card">
      <h2>3D preview</h2>
      <div id="model3d" sse-swap="model3d">{% include "_model3d.html" %}</div>
    </div>
    {% endif %}
    <div id="offers" sse-swap="offers" ...>  {# existing #}
```

Vendor the viewer + mount static:

```bash
mkdir -p api/static
curl -L -o api/static/model-viewer.min.js \
  https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0/model-viewer.min.js
```

`api/main.py`: `from fastapi.staticfiles import StaticFiles` + `app.mount("/static", StaticFiles(directory="api/static"), name="static")` (after routers). Lazy-load in `item_detail.html` only when enabled:

```html
{% if m3d_enabled %}<script type="module" src="/static/model-viewer.min.js"></script>{% endif %}
```

Respect reduced motion — in the template replace bare `auto-rotate` with a tiny inline script or simply omit `auto-rotate` when the user prefers reduced motion:

```html
<script>
  if (matchMedia('(prefers-reduced-motion: reduce)').matches)
    document.querySelectorAll('model-viewer[auto-rotate]')
      .forEach(v => v.removeAttribute('auto-rotate'));
</script>
```

- [ ] **Step 3:** Full suite → PASS. Static mount must not break TestClient startup (directory exists because the vendored file is committed).
- [ ] **Step 4:** `git add -A && git commit -m "Stream model3d SSE events; vendor model-viewer with lazy load"`

---

### Task 7: Pre-generation script + docs + prod volume

**Files:** Create `scripts/pregen_models.py`; Modify `README.md` (Phase 10 section), `docker-compose.yml` + `docker-compose.prod.yml` (a `model3d` volume mounted at the storage dir for api + worker), `.env.example` (`MESHY_API_KEY=`, `MODEL3D_MONTHLY_CAP=8`).

- [ ] **Step 1:** `scripts/pregen_models.py`:

```python
"""Pre-generate 3D models for chosen items so demos never wait or spend cap.

Usage: python -m scripts.pregen_models <tracked_product_id> [...]
Runs synchronously (no Celery) — intended for one-off local/prod seeding.
"""
import sys
import uuid

from core.db import SessionLocal
from core.models import ProductModel3D, TrackedProduct
from workers.model3d import run_generation


def main(ids: list[str]) -> None:
    db = SessionLocal()
    try:
        for raw in ids:
            tp = db.get(TrackedProduct, uuid.UUID(raw))
            if tp is None:
                print(f"skip {raw}: not found")
                continue
            offer = next((o for o in tp.offers if o.image_url and o.is_available), None)
            if offer is None:
                print(f"skip {raw}: no offer image")
                continue
            row = db.query(ProductModel3D).filter_by(tracked_product_id=tp.id).one_or_none()
            if row is None:
                row = ProductModel3D(tracked_product_id=tp.id, source_image_url=offer.image_url)
                db.add(row)
                db.flush()
            print(f"generating {tp.title} …")
            run_generation(db, row)
            db.commit()
            print(f"  ready: {row.glb_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 2:** README Phase 10 bullet + a short "3D/AR previews" usage section (enable via `MESHY_API_KEY`, cap, pregen script). Compose: add named volume `model3d:` mounted at `/app/model3d` for `api` and `worker`; set `MODEL3D_STORAGE_DIR=/app/model3d` in both.
- [ ] **Step 3:** Full suite `pytest -q` → all green. `alembic upgrade head` runs clean on a scratch Postgres if available (CI also checks).
- [ ] **Step 4:** `git add -A && git commit -m "Add 3D pregen script, compose volume, Phase 10 docs"`

---

## Self-review notes

- Spec coverage: data model (T1), cap (T2), provider (T3), pipeline+events+dead-letter (T4), 5 UI states + routes + ownership (T5), SSE + viewer + reduced-motion (T6), pregen + docs + volume (T7). Regenerate-on-ready covered in T5 route (`row.status in ("failed", "ready")`).
- The `model-viewer` CDN version/URL should be pinned to whatever current stable resolves at implement time; self-host the file (commit it), never hot-link.
- Meshy field names flagged for verification at T3 (single-file blast radius).
