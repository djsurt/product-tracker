"""Tests for the dead-letter pipeline function (Phase 5)."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import DeadLetter, Offer, TrackedProduct, User
from workers.pipeline import record_dead_letter

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
def offer(db):
    user = User(email="d@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    tp = TrackedProduct(user_id=user.id, title="T", query="t")
    db.add(tp)
    db.flush()
    o = Offer(
        tracked_product_id=tp.id, source="scraper", source_product_id="s1",
        title="T", url="http://x/buy", currency="USD",
        last_price=Decimal("10.00"), is_available=True,
    )
    db.add(o)
    db.flush()
    return o


def test_record_dead_letter_marks_offer_stale(db, offer):
    dl = record_dead_letter(db, offer.id, "fetch_offer", "boom", retries=5)

    assert offer.is_available is False  # bad price no longer trusted
    assert dl.task_name == "fetch_offer"
    assert dl.retries == 5
    assert dl.offer_id == offer.id

    count = db.scalar(select(func.count()).select_from(DeadLetter))
    assert count == 1


def test_record_dead_letter_without_offer(db):
    # A failure not tied to a surviving offer still records.
    dl = record_dead_letter(db, None, "notify", "kaboom", retries=3)
    assert dl.offer_id is None
    assert db.scalar(select(func.count()).select_from(DeadLetter)) == 1


def test_error_is_truncated(db, offer):
    dl = record_dead_letter(db, offer.id, "fetch_offer", "x" * 9000, retries=1)
    assert len(dl.error) == 4000


def test_dead_letter_can_preserve_offer_availability(db, offer):
    # A non-fetch failure (e.g. notify/SMTP) records the dead-letter but must NOT
    # condemn a perfectly-priced offer as stale.
    record_dead_letter(
        db, offer.id, "notify", "smtp down", retries=3,
        mark_offer_unavailable=False,
    )
    assert offer.is_available is True
