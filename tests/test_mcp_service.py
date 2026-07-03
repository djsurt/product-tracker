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
