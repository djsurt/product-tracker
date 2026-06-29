"""Tests for the alert rules engine (workers/notifications.py).

Like the pipeline tests, these run against in-memory SQLite with an injected
fake sender — no SMTP, no broker. That's the payoff of keeping the *logic*
(which alerts fire, debounce, what the email says) separate from the Celery
task and the SMTP transport.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from core.models import Alert, Offer, TrackedProduct, User
from workers.notifications import (
    AlertContext,
    alert_threshold_met,
    fire_alerts,
    is_debounced,
)

import core.models  # noqa: F401  (register tables on Base.metadata)


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
def product(db):
    user = User(email="buyer@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    tp = TrackedProduct(
        user_id=user.id, title="Headphones", query="headphones",
        target_price=Decimal("200.00"),
    )
    db.add(tp)
    db.flush()
    return tp


def _add_offer(db, product, price):
    offer = Offer(
        tracked_product_id=product.id,
        source="mock",
        source_product_id="sku-1",
        title="Headphones",
        url="http://store.test/buy/sku-1",
        currency="USD",
        last_price=Decimal(price),
        is_available=True,
    )
    db.add(offer)
    db.flush()
    return offer


# --- pure rule matching ----------------------------------------------------
def test_below_target_uses_threshold():
    alert = Alert(rule="below_target", threshold=Decimal("150"))
    ctx = AlertContext(best_price=Decimal("149"), new_price=Decimal("149"),
                       previous_price=Decimal("160"), target_price=None)
    assert alert_threshold_met(alert, ctx) is True
    ctx.best_price = Decimal("151")
    assert alert_threshold_met(alert, ctx) is False


def test_below_target_falls_back_to_product_target():
    alert = Alert(rule="below_target", threshold=None)
    ctx = AlertContext(best_price=Decimal("180"), new_price=Decimal("180"),
                       previous_price=Decimal("220"), target_price=Decimal("200"))
    assert alert_threshold_met(alert, ctx) is True


def test_pct_drop_matches_on_percentage():
    alert = Alert(rule="pct_drop", threshold=Decimal("10"))
    # 200 -> 150 is a 25% drop, clears a 10% rule
    ctx = AlertContext(best_price=Decimal("150"), new_price=Decimal("150"),
                       previous_price=Decimal("200"), target_price=None)
    assert alert_threshold_met(alert, ctx) is True
    # 200 -> 190 is only 5%, doesn't clear it
    ctx.new_price = Decimal("190")
    ctx.previous_price = Decimal("200")
    assert alert_threshold_met(alert, ctx) is False


def test_debounce_window():
    now = datetime.now(timezone.utc)
    alert = Alert(rule="below_target", last_fired_at=now - timedelta(seconds=30))
    assert is_debounced(alert, now, cooldown_seconds=3600) is True
    assert is_debounced(alert, now, cooldown_seconds=10) is False
    alert.last_fired_at = None
    assert is_debounced(alert, now, cooldown_seconds=3600) is False


# --- end-to-end firing (with a fake sender) --------------------------------
def test_fire_alerts_sends_and_stamps(db, product):
    offer = _add_offer(db, product, "150.00")
    alert = Alert(
        user_id=product.user_id, tracked_product_id=product.id,
        rule="below_target", threshold=Decimal("200"),
    )
    db.add(alert)
    db.flush()

    sent = []
    fired = fire_alerts(
        db,
        tracked_product_id=product.id,
        offer_id=offer.id,
        new_price=Decimal("150.00"),
        previous_price=Decimal("220.00"),
        send=lambda to, subject, body: sent.append((to, subject, body)),
    )

    assert len(fired) == 1
    assert alert.last_fired_at is not None
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "buyer@example.com"
    assert "Headphones" in subject
    assert f"/go/{offer.id}" in body  # the redirect link is present


def test_fire_alerts_respects_debounce(db, product):
    offer = _add_offer(db, product, "150.00")
    alert = Alert(
        user_id=product.user_id, tracked_product_id=product.id,
        rule="below_target", threshold=Decimal("200"),
        last_fired_at=datetime.now(timezone.utc),  # just fired
    )
    db.add(alert)
    db.flush()

    sent = []
    fired = fire_alerts(
        db, tracked_product_id=product.id, offer_id=offer.id,
        new_price=Decimal("150.00"), previous_price=Decimal("220.00"),
        send=lambda *a: sent.append(a),
    )
    assert fired == []
    assert sent == []


def test_fire_alerts_skips_inactive(db, product):
    offer = _add_offer(db, product, "150.00")
    db.add(Alert(
        user_id=product.user_id, tracked_product_id=product.id,
        rule="below_target", threshold=Decimal("200"), is_active=False,
    ))
    db.flush()

    sent = []
    fired = fire_alerts(
        db, tracked_product_id=product.id, offer_id=offer.id,
        new_price=Decimal("150.00"), previous_price=Decimal("220.00"),
        send=lambda *a: sent.append(a),
    )
    assert fired == []
    assert sent == []


def test_already_sent_alert_is_not_resent_when_a_later_send_fails(db, product):
    """A send() failure on a later alert must not cause an earlier, already-
    delivered alert to be re-emailed on the task's retry."""
    offer = _add_offer(db, product, "150.00")
    common = dict(
        user_id=product.user_id, tracked_product_id=product.id,
        rule="below_target", threshold=Decimal("200"),
    )
    a1, a2 = Alert(**common), Alert(**common)
    db.add_all([a1, a2])
    db.flush()

    calls = []

    def flaky(to, subject, body):
        calls.append(to)
        if len(calls) == 2:  # second alert's email fails
            raise RuntimeError("smtp down")

    with pytest.raises(RuntimeError):
        fire_alerts(
            db, tracked_product_id=product.id, offer_id=offer.id,
            new_price=Decimal("150.00"), previous_price=Decimal("220.00"),
            send=flaky,
        )

    # The first alert was delivered AND its debounce stamp committed; the second
    # never fired.
    assert a1.last_fired_at is not None
    assert a2.last_fired_at is None
    assert len(calls) == 2

    # On retry only the still-unsent alert fires; the delivered one is debounced.
    sent = []
    fired = fire_alerts(
        db, tracked_product_id=product.id, offer_id=offer.id,
        new_price=Decimal("150.00"), previous_price=Decimal("220.00"),
        send=lambda to, s, b: sent.append(to),
    )
    assert len(fired) == 1
    assert len(sent) == 1
