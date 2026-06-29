"""Server-rendered HTMX frontend (Phase 6).

A deliberately all-Python UI: FastAPI returns HTML (Jinja) and htmx swaps small
fragments in place — no SPA, no build step, no separate node toolchain. The JSON
API (everything else) stays intact for programmatic clients; this router is a
parallel, browser-facing face over the same domain logic and DB.

Auth here is a httponly **session cookie** carrying the same signed JWT the API
uses as a bearer token (see api/deps.py) — the natural fit for server-rendered
pages, where the browser sends the cookie automatically.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import SESSION_COOKIE, get_optional_web_user, require_web_user
from core.db import get_db
from core.models import Alert, Offer, PricePoint, TrackedProduct, User
from core.security import create_access_token, hash_password, verify_password
from core.settings import get_settings

router = APIRouter(tags=["web"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
settings = get_settings()


# --- helpers ---------------------------------------------------------------
def _set_session_cookie(resp: RedirectResponse, user_id: str) -> None:
    resp.set_cookie(
        SESSION_COOKIE,
        create_access_token(subject=user_id),
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.env != "local",  # require HTTPS in deployed envs
        path="/",
    )


def _owned(item_id: uuid.UUID, user: User, db: Session) -> TrackedProduct:
    item = db.scalar(
        select(TrackedProduct).where(
            TrackedProduct.id == item_id, TrackedProduct.user_id == user.id
        )
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return item


def _sparkline(prices: list[Decimal], w: int = 280, h: int = 40) -> str | None:
    """Render a tiny inline SVG price trend — no JS charting lib needed.

    Server-side SVG keeps the all-Python promise and means the chart arrives with
    the fragment (no extra request). Good enough for a sparkline; a real chart
    page would reach for a JS lib.
    """
    pts = [float(p) for p in prices]
    if len(pts) < 2:
        return None
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    step = w / (len(pts) - 1)
    coords = " ".join(
        f"{i * step:.1f},{h - (v - lo) / span * (h - 4) - 2:.1f}"
        for i, v in enumerate(pts)
    )
    color = "#3ecf8e" if pts[-1] <= pts[0] else "#ff6b6b"  # down = good
    return (
        f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<polyline fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" points="{coords}"/></svg>'
    )


def _best_deal(db: Session, item: TrackedProduct):
    # imported lazily to keep template/web concerns out of pipeline import graph
    from workers.pipeline import compute_best_deal

    return compute_best_deal(db, item, settings.price_history_window_days)


def _render_offers(request: Request, item: TrackedProduct, db: Session) -> HTMLResponse:
    offers = list(
        db.scalars(
            select(Offer)
            .where(Offer.tracked_product_id == item.id)
            .order_by(Offer.last_price.is_(None), Offer.last_price)
        )
    )
    deal = _best_deal(db, item)

    spark = None
    if deal.best_offer_id is not None:
        history = list(
            db.scalars(
                select(PricePoint.price)
                .where(PricePoint.offer_id == deal.best_offer_id)
                .order_by(PricePoint.observed_at.desc())
                .limit(40)
            )
        )
        spark = _sparkline(list(reversed(history)))

    return templates.TemplateResponse(
        request,
        "_offers.html",
        {"offers": offers, "deal": deal, "spark": spark},
    )


def _render_alerts(request: Request, item_id: uuid.UUID, db: Session) -> HTMLResponse:
    alerts = list(
        db.scalars(
            select(Alert)
            .where(Alert.tracked_product_id == item_id)
            .order_by(Alert.created_at.desc())
        )
    )
    return templates.TemplateResponse(request, "_alerts.html", {"alerts": alerts})


def _parse_decimal(raw: str | None) -> Decimal | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


# --- auth pages ------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(get_optional_web_user)):
    if user is not None:
        return RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"user": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"user": None, "error": "Incorrect email or password"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    resp = RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(resp, str(user.id))
    return resp


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user: User | None = Depends(get_optional_web_user)):
    if user is not None:
        return RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "register.html", {"user": None})


@router.post("/register")
def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    error = None
    if len(password) < 8:
        error = "Password must be at least 8 characters."
    elif db.scalar(select(User).where(User.email == email)) is not None:
        error = "That email is already registered."
    if error:
        return templates.TemplateResponse(
            request, "register.html", {"user": None, "error": error},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = User(email=email, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    resp = RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(resp, str(user.id))
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# --- dashboard + wishlist CRUD --------------------------------------------
@router.get("/app", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    items = list(
        db.scalars(
            select(TrackedProduct)
            .where(TrackedProduct.user_id == user.id)
            .order_by(TrackedProduct.created_at.desc())
        )
    )
    return templates.TemplateResponse(
        request, "dashboard.html", {"user": user, "items": items}
    )


@router.post("/app/items", response_class=HTMLResponse)
def create_item(
    request: Request,
    title: str = Form(...),
    query: str = Form(...),
    target_price: str | None = Form(None),
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    db.add(
        TrackedProduct(
            user_id=user.id,
            title=title.strip(),
            query=query.strip(),
            target_price=_parse_decimal(target_price),
        )
    )
    db.commit()
    items = list(
        db.scalars(
            select(TrackedProduct)
            .where(TrackedProduct.user_id == user.id)
            .order_by(TrackedProduct.created_at.desc())
        )
    )
    return templates.TemplateResponse(request, "_items.html", {"items": items})


@router.delete("/app/items/{item_id}", response_class=HTMLResponse)
def delete_item(
    request: Request,
    item_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    db.delete(_owned(item_id, user, db))
    db.commit()
    items = list(
        db.scalars(
            select(TrackedProduct)
            .where(TrackedProduct.user_id == user.id)
            .order_by(TrackedProduct.created_at.desc())
        )
    )
    return templates.TemplateResponse(request, "_items.html", {"items": items})


# --- item detail + live partials ------------------------------------------
@router.get("/app/items/{item_id}", response_class=HTMLResponse)
def item_detail(
    request: Request,
    item_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    item = _owned(item_id, user, db)
    alerts = list(
        db.scalars(
            select(Alert).where(Alert.tracked_product_id == item.id)
            .order_by(Alert.created_at.desc())
        )
    )
    return templates.TemplateResponse(
        request, "item_detail.html", {"user": user, "item": item, "alerts": alerts}
    )


@router.get("/app/items/{item_id}/offers", response_class=HTMLResponse)
def item_offers(
    request: Request,
    item_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    item = _owned(item_id, user, db)
    return _render_offers(request, item, db)


@router.post("/app/items/{item_id}/refresh", response_class=HTMLResponse)
def item_refresh(
    request: Request,
    item_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    item = _owned(item_id, user, db)
    from workers.tasks import refresh_product

    refresh_product.delay(str(item.id))  # async; the live poll picks up results
    return _render_offers(request, item, db)


@router.post("/app/items/{item_id}/alerts", response_class=HTMLResponse)
def item_add_alert(
    request: Request,
    item_id: uuid.UUID,
    rule: str = Form(...),
    threshold: str | None = Form(None),
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    item = _owned(item_id, user, db)
    parsed = _parse_decimal(threshold)
    # Mirror the JSON API's guards so unfireable rules aren't silently created.
    valid = rule in ("below_target", "pct_drop") and not (
        (rule == "below_target" and parsed is None and item.target_price is None)
        or (rule == "pct_drop" and parsed is None)
    )
    if valid:
        db.add(
            Alert(
                user_id=user.id,
                tracked_product_id=item.id,
                rule=rule,
                threshold=parsed,
            )
        )
        db.commit()
    return _render_alerts(request, item_id, db)


@router.delete("/app/items/{item_id}/alerts/{alert_id}", response_class=HTMLResponse)
def item_delete_alert(
    request: Request,
    item_id: uuid.UUID,
    alert_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    _owned(item_id, user, db)  # ownership gate
    alert = db.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.tracked_product_id == item_id)
    )
    if alert is not None:
        db.delete(alert)
        db.commit()
    return _render_alerts(request, item_id, db)
