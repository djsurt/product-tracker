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
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _product(db):
    user = User(email=f"{uuid.uuid4()}@x.com", hashed_password="h")
    db.add(user)
    db.flush()
    tp = TrackedProduct(user_id=user.id, title="XM5", query="xm5")
    db.add(tp)
    db.flush()
    return tp


def test_model3d_quota_caps(monkeypatch):
    import fakeredis

    from core.cache import model3d_quota_ok, model3d_quota_spend

    r = fakeredis.FakeRedis(decode_responses=True)
    assert model3d_quota_ok(client=r, cap=2)
    model3d_quota_spend(client=r)
    model3d_quota_spend(client=r)
    assert not model3d_quota_ok(client=r, cap=2)


def _mock_http(handler):
    import httpx

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_meshy_create_and_poll(monkeypatch):
    import httpx

    from core import meshy

    monkeypatch.setattr(meshy, "_api_key", lambda: "k")

    def handler(request):
        if request.method == "POST":
            assert b"image_url" in request.read()
            return httpx.Response(200, json={"result": "task-123"})
        return httpx.Response(
            200,
            json={
                "id": "task-123",
                "status": "SUCCEEDED",
                "model_urls": {"glb": "http://m/x.glb", "usdz": "http://m/x.usdz"},
            },
        )

    c = _mock_http(handler)
    task_id = meshy.create_image_to_3d_task("http://i/x.jpg", client=c)
    assert task_id == "task-123"
    task = meshy.get_task(task_id, client=c)
    assert task.status == "SUCCEEDED"
    assert task.model_urls["glb"].endswith(".glb")


def test_meshy_download(tmp_path, monkeypatch):
    import httpx

    from core import meshy

    def handler(request):
        return httpx.Response(200, content=b"GLBDATA")

    meshy.download_file(
        "http://m/x.glb", tmp_path / "x.glb", client=_mock_http(handler)
    )
    assert (tmp_path / "x.glb").read_bytes() == b"GLBDATA"


def test_run_generation_happy_path(db, monkeypatch, tmp_path):
    from core import meshy
    from workers.model3d import run_generation

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
    assert (tmp_path / f"{tp.id}.glb").exists()


def test_run_generation_provider_failure_marks_failed(db, monkeypatch):
    from core import meshy
    from workers.model3d import GenerationFailed, run_generation

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
    with pytest.raises(GenerationFailed):
        run_generation(db, row)
    assert row.status == "failed"
    assert "bad photo" in row.error


def test_model3d_event_channel_contract():
    from core.events import model3d_update_channel, price_update_channel

    assert model3d_update_channel("x") == "model3d_updates:x"
    assert price_update_channel("x") == "price_updates:x"


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
    db.rollback()


# --- web routes (use the shared client fixture) -----------------------------
def _web_item(client):
    import re

    client.post(
        "/register",
        data={"email": f"{uuid.uuid4()}@x.com", "password": "supersecret"},
        follow_redirects=False,
    )
    client.post("/app/items", data={"title": "XM5", "query": "xm5"})
    dash = client.get("/app").text
    return re.search(r"/app/items/([0-9a-f-]{36})", dash).group(1)


def test_model3d_dark_without_key(client):
    item_id = _web_item(client)
    page = client.get(f"/app/items/{item_id}")
    assert page.status_code == 200
    assert "model3d" not in page.text  # zero UI surface when unconfigured
    resp = client.post(f"/app/items/{item_id}/model3d")
    assert resp.status_code == 503


def test_model3d_trigger_enqueues_and_shows_generating(client, monkeypatch):
    from api.routers import web as web_mod

    monkeypatch.setattr(web_mod.settings, "meshy_api_key", "k")
    monkeypatch.setattr(web_mod, "model3d_quota_ok", lambda: True)
    monkeypatch.setattr(web_mod, "model3d_quota_spend", lambda: None)
    monkeypatch.setattr(
        web_mod, "_item_image", lambda item, best_offer_id=None: "http://i/x.jpg"
    )
    calls = []
    monkeypatch.setattr(
        "workers.tasks.generate_model3d.delay", lambda *a: calls.append(a)
    )
    item_id = _web_item(client)
    resp = client.post(f"/app/items/{item_id}/model3d")
    assert resp.status_code == 200
    assert "Sculpting" in resp.text
    assert len(calls) == 1


def test_model3d_cap_reached_state(client, monkeypatch):
    from api.routers import web as web_mod

    monkeypatch.setattr(web_mod.settings, "meshy_api_key", "k")
    monkeypatch.setattr(web_mod, "model3d_quota_ok", lambda: False)
    monkeypatch.setattr(
        web_mod, "_item_image", lambda item, best_offer_id=None: "http://i/x.jpg"
    )
    calls = []
    monkeypatch.setattr(
        "workers.tasks.generate_model3d.delay", lambda *a: calls.append(a)
    )
    item_id = _web_item(client)
    resp = client.post(f"/app/items/{item_id}/model3d")
    assert resp.status_code == 200
    assert "3D budget" in resp.text
    assert calls == []


def test_model_file_requires_ownership_and_existence(client, monkeypatch):
    from api.routers import web as web_mod

    monkeypatch.setattr(web_mod.settings, "meshy_api_key", "k")
    item_id = _web_item(client)
    # no model row yet -> 404 even for the owner
    assert client.get(f"/app/models/{item_id}.glb").status_code == 404
    # a different user gets 404 by ownership
    client.cookies.clear()
    client.post(
        "/register",
        data={"email": "other@x.com", "password": "supersecret"},
        follow_redirects=False,
    )
    assert client.get(f"/app/models/{item_id}.glb").status_code == 404
    assert client.get(f"/app/models/{item_id}.exe").status_code == 404
