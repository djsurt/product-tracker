"""Tests for the pipeline logic, using a fake in-memory source (no network, no
broker). This is exactly why pipeline.py takes an explicit Session and a list of
sources instead of reaching for globals — it's trivially testable.
"""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import Offer, PricePoint, TrackedProduct, User
from sources.base import NormalizedOffer
from workers.pipeline import discover_offers, record_price

import core.models  # noqa: F401  (register tables on Base.metadata)


class FakeSource:
    """A deterministic stand-in for a real price source."""

    name = "fake"

    def __init__(self, price="100.00"):
        self.price = Decimal(price)

    def search(self, query):
        return [
            NormalizedOffer(
                source=self.name,
                source_product_id=f"{query}-x",
                title=f"{query} X",
                price=self.price,
                currency="USD",
                url="http://x.test/buy/x",
                available=True,
            )
        ]

    def fetch(self, source_product_id):
        return NormalizedOffer(
            source=self.name,
            source_product_id=source_product_id,
            title="X",
            price=self.price,
            currency="USD",
            url="http://x.test/buy/x",
            available=True,
        )


class BrokenSource:
    name = "broken"

    def search(self, query):
        raise TimeoutError("source timed out")

    def fetch(self, source_product_id):
        raise TimeoutError("source timed out")


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
def tracked_product(db):
    user = User(email="p@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    tp = TrackedProduct(user_id=user.id, title="Thing", query="thing")
    db.add(tp)
    db.flush()
    return tp


def test_discover_creates_offers(db, tracked_product):
    offers = discover_offers(db, tracked_product, [FakeSource()])
    assert len(offers) == 1
    assert offers[0].source == "fake"


def test_discover_is_idempotent(db, tracked_product):
    discover_offers(db, tracked_product, [FakeSource()])
    discover_offers(db, tracked_product, [FakeSource()])  # run again
    count = db.scalar(select(func.count()).select_from(Offer))
    assert count == 1  # no duplicate despite running twice


def test_discover_continues_when_one_source_fails(db, tracked_product):
    offers = discover_offers(db, tracked_product, [FakeSource(), BrokenSource()])

    assert len(offers) == 1
    assert offers[0].source == "fake"
    assert db.scalar(select(func.count()).select_from(Offer)) == 1


def test_record_price_updates_snapshot_and_appends_history(db, tracked_product):
    offer = discover_offers(db, tracked_product, [FakeSource()])[0]

    record_price(db, offer, FakeSource("120.00").fetch(offer.source_product_id))
    record_price(db, offer, FakeSource("90.00").fetch(offer.source_product_id))

    # snapshot reflects the latest observation
    assert offer.last_price == Decimal("90.00")
    assert offer.is_available is True
    assert offer.last_seen_at is not None

    # history is append-only: two rows, both prices retained
    points = db.scalars(
        select(PricePoint).where(PricePoint.offer_id == offer.id)
    ).all()
    assert len(points) == 2
    assert {p.price for p in points} == {Decimal("120.00"), Decimal("90.00")}
