"""Pure notification logic, separated from the Celery `notify` task.

Same split as workers/pipeline.py vs workers/tasks.py: the rules engine here
takes an explicit `Session` and an injectable `send` callable, so it can be
unit-tested against SQLite with a fake sender — no SMTP, no broker, no network.

The flow Phase 4 demonstrates is **event-driven decoupling**: `fetch_offer`
doesn't know or care who gets emailed; it just records a price and, when that
price dropped, enqueues a `notify` event. This module is the consumer that
turns that raw event into "which of the user's alert rules actually fire, and
have we already told them recently?".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Alert, Offer, TrackedProduct
from core.settings import get_settings
from workers.pipeline import compute_best_deal

# A sender is anything callable as send(to, subject, body). Injecting it keeps
# this module testable and unaware of the email transport.
Sender = Callable[[str, str, str], None]


@dataclass
class AlertContext:
    """Everything an alert rule needs to decide whether to fire."""

    best_price: Decimal | None      # cheapest available offer right now
    new_price: Decimal              # the price that just dropped
    previous_price: Decimal | None  # that offer's price before this drop
    target_price: Decimal | None    # the product's target, if any
    currency: str | None = None     # currency of best_price (for display)


def alert_threshold_met(alert: Alert, ctx: AlertContext) -> bool:
    """Does this alert's rule match the current price context?

    - **below_target**: the best available price is at/under the threshold
      (falling back to the product's target_price when the alert has none).
    - **pct_drop**: the offer that just changed fell by at least `threshold`
      percent versus its previous price.
    """
    if alert.rule == "below_target":
        target = alert.threshold if alert.threshold is not None else ctx.target_price
        if target is None or ctx.best_price is None:
            return False
        return ctx.best_price <= target

    if alert.rule == "pct_drop":
        if alert.threshold is None or not ctx.previous_price:
            return False
        drop_pct = (ctx.previous_price - ctx.new_price) / ctx.previous_price * 100
        return drop_pct >= alert.threshold

    return False  # unknown rule -> never fire


def is_debounced(alert: Alert, now: datetime, cooldown_seconds: int) -> bool:
    """True if we fired this alert too recently to fire it again."""
    if alert.last_fired_at is None:
        return False
    last = alert.last_fired_at
    if last.tzinfo is None:  # SQLite returns naive datetimes; treat as UTC
        last = last.replace(tzinfo=timezone.utc)
    return now - last < timedelta(seconds=cooldown_seconds)


# Display symbols for the currencies our sources can emit. We never assume the
# price is in dollars — labeling a £49.99 offer as "$49.99" would misrepresent
# the deal — so unknown codes fall back to a "<amount> <CODE>" form.
_CURRENCY_SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€"}


def _money(amount: Decimal | None, currency: str | None) -> str:
    code = currency or "USD"
    symbol = _CURRENCY_SYMBOLS.get(code)
    return f"{symbol}{amount}" if symbol else f"{amount} {code}"


def render_alert_email(
    product: TrackedProduct, alert: Alert, ctx: AlertContext, go_url: str
) -> tuple[str, str]:
    """Build (subject, body) for a fired alert."""
    currency = ctx.currency
    price = ctx.best_price if ctx.best_price is not None else ctx.new_price
    subject = f"Price drop: {product.title} is now {_money(price, currency)}"
    lines = [
        f"Good news — {product.title} just dropped in price.",
        "",
        f"  Best price now: {_money(price, currency)}",
    ]
    if ctx.previous_price is not None:
        lines.append(f"  Was:            {_money(ctx.previous_price, currency)}")
    if alert.rule == "below_target":
        target = alert.threshold if alert.threshold is not None else ctx.target_price
        lines.append(f"  Your target:    {_money(target, currency)}")
    elif alert.rule == "pct_drop":
        lines.append(f"  Alert rule:     {alert.threshold}% or larger drop")
    lines += [
        "",
        f"Buy it: {go_url}",
        "",
        "— Deal Hunter",
    ]
    return subject, "\n".join(lines)


def fire_alerts(
    db: Session,
    *,
    tracked_product_id,
    offer_id,
    new_price: Decimal,
    previous_price: Decimal | None,
    send: Sender,
    now: datetime | None = None,
) -> list[Alert]:
    """Evaluate a product's active alerts against a price drop and notify.

    Returns the alerts that actually fired (matched a rule, weren't debounced,
    and were emailed). Each fired alert's `last_fired_at` is stamped AND
    committed immediately after its email is sent, so a failure while sending a
    *later* alert can't roll back an already-delivered one and cause a duplicate
    email on the task's retry. Earlier alerts stay debounced; only the
    still-unsent ones are retried by the caller.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)

    product = db.get(TrackedProduct, tracked_product_id)
    if product is None:
        return []
    offer = db.get(Offer, offer_id)

    deal = compute_best_deal(db, product, settings.price_history_window_days)
    ctx = AlertContext(
        best_price=deal.best_price,
        new_price=new_price,
        previous_price=previous_price,
        target_price=product.target_price,
        currency=deal.currency,
    )

    alerts = list(
        db.scalars(
            select(Alert).where(
                Alert.tracked_product_id == product.id,
                Alert.is_active.is_(True),
            )
        )
    )

    # Email goes to the offer the user would buy from (the current best deal),
    # falling back to the offer that triggered the event.
    link_offer_id = deal.best_offer_id or (offer.id if offer else offer_id)
    go_url = f"{settings.app_base_url}/go/{link_offer_id}"

    fired: list[Alert] = []
    for alert in alerts:
        if not alert_threshold_met(alert, ctx):
            continue
        if is_debounced(alert, now, settings.alert_debounce_seconds):
            continue
        if alert.channel != "email":
            continue  # only email is implemented in Phase 4

        subject, body = render_alert_email(product, alert, ctx, go_url)
        send(product.user.email, subject, body)
        # Persist the debounce stamp right after a successful send so it survives
        # even if a later alert's send() raises and the task retries.
        alert.last_fired_at = now
        db.commit()
        fired.append(alert)

    return fired
