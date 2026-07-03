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

    # first get_message() may consume the subscribe confirmation and return
    # None (redis-py returns None for ignored messages, it doesn't skip them)
    msg = sub.get_message(timeout=1) or sub.get_message(timeout=1)
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
