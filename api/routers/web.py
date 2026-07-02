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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import SESSION_COOKIE, get_optional_web_user, require_web_user
from api.jobs import enqueue_product_refresh
from core import metrics, vision
from core.db import get_db
from core.models import Alert, DeadLetter, Offer, PricePoint, TrackedProduct, User
from core.security import create_access_token, hash_password, verify_password
from core.settings import get_settings
from sources.registry import active_source_names

router = APIRouter(tags=["web"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
settings = get_settings()

DEMO_ITEMS = [
    ("Sony WH-1000XM5", "sony wh-1000xm5", Decimal("299.99")),
    ("Nintendo Switch OLED", "nintendo switch oled", Decimal("299.99")),
    ("Instant Pot Duo", "instant pot duo", Decimal("79.99")),
]


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


def _sparkline(prices: list[Decimal], w: int = 560, h: int = 60) -> str | None:
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
    xy = [
        (i * step, h - (v - lo) / span * (h - 6) - 3)
        for i, v in enumerate(pts)
    ]
    coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    # down (cheaper) is good; align with the theme's semantic green / red
    color = "#1f9d63" if pts[-1] <= pts[0] else "#cf4436"  # down = good
    fill_id = f"sparkfill{id(prices) & 0xffff}"
    area = f"0,{h:.0f} {coords} {xy[-1][0]:.1f},{h:.0f}"
    ex, ey = xy[-1]
    return (
        f'<svg class="spark" width="100%" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="Price trend">'
        f'<defs><linearGradient id="{fill_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{color}" stop-opacity="0.16"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'<polygon fill="url(#{fill_id})" points="{area}"/>'
        f'<polyline fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{coords}"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3" fill="{color}"/></svg>'
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


def _count(db: Session, stmt) -> int:
    return int(db.scalar(stmt) or 0)


def _ops_context(request: Request, user: User, db: Session) -> dict:
    items = list(
        db.scalars(
            select(TrackedProduct)
            .where(TrackedProduct.user_id == user.id)
            .order_by(TrackedProduct.created_at.desc())
        )
    )
    item_ids = [item.id for item in items]
    if item_ids:
        offers_stmt = select(func.count(Offer.id)).where(
            Offer.tracked_product_id.in_(item_ids)
        )
        price_points_stmt = (
            select(func.count(PricePoint.id))
            .join(Offer, Offer.id == PricePoint.offer_id)
            .where(Offer.tracked_product_id.in_(item_ids))
        )
        recent_offers = list(
            db.scalars(
                select(Offer)
                .where(Offer.tracked_product_id.in_(item_ids))
                .order_by(Offer.last_seen_at.desc(), Offer.created_at.desc())
                .limit(12)
            )
        )
    else:
        offers_stmt = select(func.count(Offer.id)).where(False)
        price_points_stmt = select(func.count(PricePoint.id)).where(False)
        recent_offers = []

    dead_letters = list(
        db.scalars(select(DeadLetter).order_by(DeadLetter.created_at.desc()).limit(8))
    )
    dead_letter_count = _count(db, select(func.count(DeadLetter.id)))
    return {
        "request": request,
        "user": user,
        "items": items,
        "recent_offers": recent_offers,
        "dead_letters": dead_letters,
        "active_sources": active_source_names(),
        "refresh_interval_seconds": settings.refresh_interval_seconds,
        "stats": {
            "tracked_items": len(items),
            "active_items": sum(1 for item in items if item.is_active),
            "offers": _count(db, offers_stmt),
            "price_points": _count(db, price_points_stmt),
            "dead_letters": dead_letter_count,
        },
        "metric_values": metrics.snapshot(),
    }


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


@router.get("/app/ops", response_class=HTMLResponse)
def ops_dashboard(
    request: Request,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "ops_dashboard.html", _ops_context(request, user, db)
    )


@router.post("/app/ops/seed-demo")
def seed_demo_items(
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    existing_queries = set(
        db.scalars(select(TrackedProduct.query).where(TrackedProduct.user_id == user.id))
    )
    created_items: list[TrackedProduct] = []
    for title, query, target in DEMO_ITEMS:
        if query in existing_queries:
            continue
        item = TrackedProduct(
            user_id=user.id,
            title=title,
            query=query,
            target_price=target,
        )
        db.add(item)
        created_items.append(item)
    db.commit()
    for item in created_items:
        enqueue_product_refresh(item.id)
    return RedirectResponse("/app/ops", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/app/ops/refresh-all")
def refresh_all_items(
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    item_ids = list(
        db.scalars(
            select(TrackedProduct.id).where(
                TrackedProduct.user_id == user.id,
                TrackedProduct.is_active.is_(True),
            )
        )
    )
    for item_id in item_ids:
        enqueue_product_refresh(item_id)
    return RedirectResponse("/app/ops", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/app/items", response_class=HTMLResponse)
def create_item(
    request: Request,
    title: str = Form(...),
    query: str = Form(...),
    target_price: str | None = Form(None),
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    item = TrackedProduct(
        user_id=user.id,
        title=title.strip(),
        query=query.strip(),
        target_price=_parse_decimal(target_price),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    enqueue_product_refresh(item.id)
    items = list(
        db.scalars(
            select(TrackedProduct)
            .where(TrackedProduct.user_id == user.id)
            .order_by(TrackedProduct.created_at.desc())
        )
    )
    return templates.TemplateResponse(request, "_items.html", {"items": items})


@router.post("/app/items/identify", response_class=HTMLResponse)
def identify_screenshot(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_web_user),
):
    """Screenshot → prefilled add-item form (HTMX fragment).

    Always 200: HTMX only swaps 2xx responses, so errors ride inside the
    fragment as a message plus an empty manual form.
    """
    ident = None
    error = None
    if file.content_type not in vision.ALLOWED_IMAGE_TYPES:
        error = "That file type isn't supported — upload a PNG, JPEG, WebP, or GIF."
    else:
        data = file.file.read(vision.MAX_IMAGE_BYTES + 1)
        if len(data) > vision.MAX_IMAGE_BYTES:
            error = "Image is over 5 MB — crop or resize it and try again."
        else:
            try:
                ident = vision.identify_product(data, file.content_type)
            except vision.VisionNotConfigured:
                error = "Screenshot identification isn't configured on this server."
            except vision.VisionUnavailable:
                error = "Identification failed — try again, or add the item manually."
            else:
                if not ident.identified:
                    ident = None
                    error = "Couldn't spot a product in that image — fill the form manually."
    return templates.TemplateResponse(
        request, "_identify_result.html", {"ident": ident, "error": error}
    )


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
