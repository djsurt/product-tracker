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
