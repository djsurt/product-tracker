"""Tests for best-deal selection, the lowest-in-window query, and verdicts."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import Offer, PricePoint, TrackedProduct, User
from workers.pipeline import _verdict, compute_best_deal, lowest_in_window

import core.models  # noqa: F401


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def _seed(db, target=None, a_price="100", b_price="80", history=("120", "70")):
    user = User(email="bd@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    tp = TrackedProduct(
        user_id=user.id,
        title="T",
        query="t",
        target_price=Decimal(target) if target else None,
    )
    db.add(tp)
    db.flush()
    a = Offer(
        tracked_product_id=tp.id, source="mock", source_product_id="a", title="A",
        url="u", currency="USD", last_price=Decimal(a_price), is_available=True,
    )
    b = Offer(
        tracked_product_id=tp.id, source="mock", source_product_id="b", title="B",
        url="u", currency="USD", last_price=Decimal(b_price), is_available=True,
    )
    db.add_all([a, b])
    db.flush()
    db.add_all([
        PricePoint(offer_id=a.id, price=Decimal(history[0])),
        PricePoint(offer_id=b.id, price=Decimal(history[1])),
    ])
    db.flush()
    return tp, a, b


def test_best_offer_is_cheapest_available(db):
    tp, a, b = _seed(db, a_price="100", b_price="80")
    deal = compute_best_deal(db, tp, window_days=30)
    assert deal.best_offer_id == b.id        # 80 < 100
    assert deal.best_price == Decimal("80")


def test_unavailable_offer_is_ignored(db):
    tp, a, b = _seed(db, a_price="100", b_price="80")
    b.is_available = False
    db.flush()
    deal = compute_best_deal(db, tp, window_days=30)
    assert deal.best_offer_id == a.id        # b is cheaper but out of stock


def test_lowest_in_window_uses_history(db):
    tp, a, b = _seed(db, history=("120", "70"))
    low = lowest_in_window(db, [a.id, b.id], days=30)
    assert low == Decimal("70")              # cheapest observed across both offers


def test_verdicts():
    # hit the user's target price -> below_target wins
    assert _verdict(Decimal("80"), Decimal("70"), Decimal("85")) == "below_target"
    # at/under the window low
    assert _verdict(Decimal("70"), Decimal("70"), None) == "all_time_low"
    # within 5% of the window low
    assert _verdict(Decimal("73"), Decimal("70"), None) == "great"
    # otherwise
    assert _verdict(Decimal("90"), Decimal("70"), None) == "fair"
    # no price at all
    assert _verdict(None, None, None) == "no_data"
