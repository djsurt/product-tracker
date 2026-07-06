"""Delisted offers: a 404/410 on fetch means the listing is GONE at the source.

That's a permanent business fact, not a transient failure — so the policy under
test is: fetch_offer marks the offer delisted immediately (no retries, no
dead-letter), the sweep stops re-enqueueing it, and discovery revives it only
if the source's search ever returns the same listing id again.
"""

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import DeadLetter, Offer, TrackedProduct, User
from sources.base import ListingGoneError, NormalizedOffer
from workers.pipeline import discover_offers, mark_offer_delisted

import core.models  # noqa: F401


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
    user = User(email="d@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    tp = TrackedProduct(user_id=user.id, title="T", query="t")
    db.add(tp)
    db.flush()
    return tp


def _offer(db, tp, source="fake", spid="x1", **kw):
    o = Offer(
        tracked_product_id=tp.id, source=source, source_product_id=spid,
        title="T", url="http://x.test/x", currency="USD",
        last_price=Decimal("10.00"), **kw,
    )
    db.add(o)
    db.flush()
    return o


class GoneSource:
    name = "fake"

    def fetch(self, source_product_id):
        raise ListingGoneError(f"{source_product_id}: 404")


class FoundSource:
    """Search returns one fixed listing id — used to prove revival."""

    name = "fake"

    def __init__(self, spid="x1"):
        self.spid = spid

    def search(self, query):
        return [
            NormalizedOffer(
                source=self.name, source_product_id=self.spid, title="T",
                price=Decimal("9.00"), currency="USD", url="http://x.test/x",
                available=True,
            )
        ]


class EmptySource:
    name = "fake"

    def search(self, query):
        return []


def test_mark_offer_delisted(db, tracked_product):
    offer = _offer(db, tracked_product)
    mark_offer_delisted(db, offer)
    assert offer.is_delisted is True
    assert offer.is_available is False  # best-deal must not trust its price


def test_fetch_offer_delists_gone_listing_without_retry_or_dead_letter(
    monkeypatch, db, tracked_product
):
    offer = _offer(db, tracked_product)
    db.commit()
    published: list[str] = []

    @contextmanager
    def fake_lock(*args, **kwargs):
        yield True

    monkeypatch.setattr("workers.tasks.SessionLocal", lambda: db)
    monkeypatch.setattr("workers.tasks.offer_lock", fake_lock)
    monkeypatch.setattr(
        "workers.tasks.RateLimiter",
        lambda *a, **k: SimpleNamespace(allow=lambda source: True),
    )
    monkeypatch.setattr("workers.tasks.get_source", lambda name: GoneSource())
    monkeypatch.setattr("workers.tasks.invalidate_best_deal", lambda *a, **k: None)
    monkeypatch.setattr(
        "workers.tasks.publish_price_update", lambda tp_id: published.append(tp_id)
    )

    from workers.tasks import fetch_offer

    result = fetch_offer(str(offer.id))

    assert result == "gone: delisted"
    offer = db.get(Offer, offer.id)  # re-read: the task closed the session
    assert offer.is_delisted is True
    assert offer.is_available is False
    # Permanent absence is a business fact, not an operational failure:
    assert db.scalar(select(func.count()).select_from(DeadLetter)) == 0
    # The UI should re-render the offers panel to show it's gone.
    assert published == [str(tracked_product.id)]


def test_discover_skips_delisted_offers(db, tracked_product):
    _offer(db, tracked_product, spid="dead", is_delisted=True, is_available=False)
    alive = _offer(db, tracked_product, spid="alive")

    offers = discover_offers(db, tracked_product, [EmptySource()])

    assert [o.id for o in offers] == [alive.id]  # the sweep never fetches "dead"


def test_discover_revives_delisted_offer_when_search_finds_it(db, tracked_product):
    dead = _offer(db, tracked_product, spid="x1", is_delisted=True, is_available=False)

    offers = discover_offers(db, tracked_product, [FoundSource(spid="x1")])

    db.refresh(dead)
    assert dead.is_delisted is False  # search proved the listing exists again
    assert dead.id in [o.id for o in offers]
