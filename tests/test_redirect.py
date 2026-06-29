"""Tests for the /go/{offer_id} redirect (Phase 4).

Self-contained fixture (own SQLite engine + dependency override) because the
test needs to seed an Offer directly — offers are created by the pipeline, not
via the API — and to read back the click_events row afterwards.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from core.db import Base, get_db
from core.models import ClickEvent, Offer, TrackedProduct, User

import core.models  # noqa: F401


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, Session
    app.dependency_overrides.clear()


def _seed_offer(Session, url="http://store.test/buy/sku-1") -> uuid.UUID:
    db = Session()
    try:
        user = User(email="u@example.com", hashed_password="x")
        db.add(user)
        db.flush()
        tp = TrackedProduct(user_id=user.id, title="T", query="t")
        db.add(tp)
        db.flush()
        offer = Offer(
            tracked_product_id=tp.id, source="mock", source_product_id="sku-1",
            title="T", url=url, currency="USD", last_price=Decimal("10.00"),
        )
        db.add(offer)
        db.commit()
        return offer.id
    finally:
        db.close()


def test_go_redirects_and_logs_click(ctx):
    client, Session = ctx
    url = "http://store.test/buy/sku-1"
    offer_id = _seed_offer(Session, url=url)

    resp = client.get(f"/go/{offer_id}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == url

    db = Session()
    try:
        count = db.scalar(select(func.count()).select_from(ClickEvent))
        assert count == 1
    finally:
        db.close()


def test_go_unknown_offer_404(ctx):
    client, _ = ctx
    resp = client.get(f"/go/{uuid.uuid4()}", follow_redirects=False)
    assert resp.status_code == 404


def test_go_rejects_non_http_url(ctx):
    # offer.url is externally sourced; a dangerous scheme must not be redirected
    # to (would make this trusted link an XSS/phishing vector).
    client, Session = ctx
    offer_id = _seed_offer(Session, url="javascript:alert(1)")
    resp = client.get(f"/go/{offer_id}", follow_redirects=False)
    assert resp.status_code == 404


def test_go_rejects_empty_url(ctx):
    client, Session = ctx
    offer_id = _seed_offer(Session, url="")
    resp = client.get(f"/go/{offer_id}", follow_redirects=False)
    assert resp.status_code == 404
