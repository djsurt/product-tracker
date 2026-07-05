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
from functools import partial
from pathlib import Path

from datetime import datetime, timezone

import anyio.to_thread
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import SESSION_COOKIE, _user_from_cookie, get_optional_web_user, require_web_user
from core.events import price_update_channel
from api.jobs import enqueue_product_refresh
from core import metrics, vision
from core.db import get_db
from core.models import Alert, ApiToken, DeadLetter, Offer, PricePoint, TrackedProduct, User
from core.security import create_access_token, hash_password, verify_password
from core.tokens import generate_token
from core.settings import get_settings
from sources.registry import active_source_names

router = APIRouter(tags=["web"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
settings = get_settings()


def _reltime(dt: datetime | None) -> str:
    """Human-relative timestamp ("4 min ago") — recency at a glance beats ISO."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hr ago" if hours == 1 else f"{hours} hrs ago"
    days = int(seconds // 86400)
    return "yesterday" if days == 1 else f"{days} days ago"


templates.env.filters["reltime"] = _reltime

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
        # pathLength=100 lets CSS animate a draw-in with a fixed dasharray
        f'<polyline fill="none" stroke="{color}" stroke-width="2" pathLength="100" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{coords}"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3" fill="{color}"/></svg>'
    )


def _best_deal(db: Session, item: TrackedProduct):
    # imported lazily to keep template/web concerns out of pipeline import graph
    from workers.pipeline import compute_best_deal

    return compute_best_deal(db, item, settings.price_history_window_days)


def _item_image(item: TrackedProduct, best_offer_id=None) -> str | None:
    """Pick the item's display image: the best offer's, else any offer's."""
    if best_offer_id is not None:
        for o in item.offers:
            if o.id == best_offer_id and o.image_url:
                return o.image_url
    return next((o.image_url for o in item.offers if o.image_url), None)


def _items_context(user: User, db: Session) -> dict:
    """Wishlist rows + each item's best deal, so the list answers "good price?"
    without a click-through. Per-item queries are fine at wishlist scale."""
    items = list(
        db.scalars(
            select(TrackedProduct)
            .where(TrackedProduct.user_id == user.id)
            .order_by(TrackedProduct.created_at.desc())
        )
    )
    deals = {item.id: _best_deal(db, item) for item in items}
    images = {
        item.id: _item_image(item, deals[item.id].best_offer_id) for item in items
    }
    return {"items": items, "deals": deals, "images": images}


def _render_items(request: Request, user: User, db: Session) -> HTMLResponse:
    return templates.TemplateResponse(request, "_items.html", _items_context(user, db))


def _offers_context(item: TrackedProduct, db: Session) -> dict:
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

    return {
        "offers": offers,
        "deal": deal,
        "spark": spark,
        "item": item,
        "item_image": _item_image(item, deal.best_offer_id),
    }


def _render_offers(request: Request, item: TrackedProduct, db: Session) -> HTMLResponse:
    return templates.TemplateResponse(request, "_offers.html", _offers_context(item, db))


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
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "tokens": _user_tokens(db, user), **_items_context(user, db)},
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
    return _render_items(request, user, db)


@router.post("/app/items/track-url", response_class=HTMLResponse)
def track_by_url(
    request: Request,
    url: str = Form(...),
    target_price: str | None = Form(None),
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    """Paste any product page URL → scrape its structured data → track it.

    Always 200 (HTMX only swaps 2xx): failures ride inside the fragment. On
    success the fragment also carries an out-of-band swap that refreshes the
    wishlist, and a normal refresh is enqueued so the *other* sources get
    searched for the same product by name.
    """
    import httpx

    from sources._http import SourceBlockedError
    from sources.registry import get_source
    from workers.pipeline import record_price

    url = url.strip()
    error = None
    observed = None
    try:
        observed = get_source("web").fetch(url)
    except KeyError:
        error = "Track-by-URL isn't enabled on this server."
    except SourceBlockedError:
        error = "That store blocks automated access, so we can't track it directly. If it's on eBay or one of our API sources, try the marketplace search instead."
    except ValueError:
        error = "Couldn't find a price on that page — the store may not publish product data. Try adding the item by name above instead."
    except httpx.HTTPError:
        error = "Couldn't reach that page. Check the link and try again."

    if error:
        return templates.TemplateResponse(
            request, "_track_url_result.html", {"error": error, "item": None}
        )

    # Idempotent per (user, url): pasting the same link twice links to the
    # existing item rather than spawning a duplicate watcher.
    existing = db.scalar(
        select(Offer)
        .join(TrackedProduct, TrackedProduct.id == Offer.tracked_product_id)
        .where(
            TrackedProduct.user_id == user.id,
            Offer.source == "web",
            Offer.source_product_id == url,
        )
    )
    if existing is not None:
        return templates.TemplateResponse(
            request,
            "_track_url_result.html",
            {"error": None, "item": existing.tracked_product, "already": True},
        )

    item = TrackedProduct(
        user_id=user.id,
        title=observed.title[:255],
        query=observed.title[:512],  # lets the other sources find it by name
        target_price=_parse_decimal(target_price),
    )
    db.add(item)
    db.flush()
    offer = Offer(
        tracked_product_id=item.id,
        source=observed.source,
        source_product_id=observed.source_product_id,
        title=observed.title[:255],
        url=observed.url,
        currency=observed.currency,
        image_url=observed.image_url,
    )
    db.add(offer)
    db.flush()
    record_price(db, offer, observed)  # first price point lands immediately
    db.commit()
    enqueue_product_refresh(item.id)
    return templates.TemplateResponse(
        request,
        "_track_url_result.html",
        {
            "error": None,
            "item": item,
            "already": False,
            "price": observed.price,
            **_items_context(user, db),
        },
    )


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
    return _render_items(request, user, db)


@router.post("/app/items/{item_id}/toggle", response_class=HTMLResponse)
def toggle_item(
    request: Request,
    item_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    """Pause/resume tracking. Resuming re-enqueues a refresh so the user sees
    fresh prices right away instead of waiting for the next scheduled sweep."""
    item = _owned(item_id, user, db)
    item.is_active = not item.is_active
    db.commit()
    if item.is_active:
        enqueue_product_refresh(item.id)
    return _render_items(request, user, db)


@router.post("/app/items/{item_id}/target")
def update_target_price(
    item_id: uuid.UUID,
    target_price: str | None = Form(None),
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    """Set or clear the target price. Full redirect (not a fragment): the
    verdict, savings line, and alert fallbacks all depend on the target."""
    item = _owned(item_id, user, db)
    item.target_price = _parse_decimal(target_price)
    db.commit()
    return RedirectResponse(f"/app/items/{item_id}", status_code=status.HTTP_303_SEE_OTHER)


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


# --- Marketplace: live search across sources, one click to track ------------
@router.get("/app/marketplace", response_class=HTMLResponse)
def marketplace_page(
    request: Request,
    user: User = Depends(require_web_user),
):
    # "web" is URL-fed (track-by-URL), never part of query fan-out — listing it
    # in "Searching …" would promise results it can't produce.
    searchable = [n for n in active_source_names() if n != "web"]
    return templates.TemplateResponse(
        request,
        "marketplace.html",
        {"user": user, "active_sources": searchable},
    )


@router.post("/app/marketplace/search", response_class=HTMLResponse)
def marketplace_search(
    request: Request,
    query: str = Form(""),
    user: User = Depends(require_web_user),
):
    """Fan out to the live sources and render the results fragment.

    Reuses the MCP service's search (same caps, same graceful per-source
    degradation) so the web UI and Claude see identical marketplace results.
    Sync route: FastAPI runs it in the threadpool, where the sources' blocking
    HTTP clients are fine.
    """
    from api.mcp_service import svc_search_deals

    query = query.strip()
    if not query:
        return templates.TemplateResponse(
            request,
            "_marketplace_results.html",
            {"results": None, "failed_sources": [], "query": ""},
        )
    found = svc_search_deals(query)
    results = sorted(found["results"], key=lambda r: r["price"])
    return templates.TemplateResponse(
        request,
        "_marketplace_results.html",
        {"results": results, "failed_sources": found["failed_sources"], "query": query},
    )


@router.post("/app/marketplace/track", response_class=HTMLResponse)
def marketplace_track(
    request: Request,
    title: str = Form(...),
    query: str = Form(...),
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    """One-click track from a search result. Idempotent per (user, query):
    tracking the same search twice links to the existing item instead of
    creating a duplicate watcher."""
    query = query.strip()
    item = db.scalar(
        select(TrackedProduct).where(
            TrackedProduct.user_id == user.id, TrackedProduct.query == query
        )
    )
    if item is None:
        item = TrackedProduct(
            user_id=user.id, title=title.strip()[:255], query=query
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        enqueue_product_refresh(item.id)
    return templates.TemplateResponse(request, "_market_tracked.html", {"item": item})


# --- API tokens for the MCP endpoint (Phase 8) ------------------------------
def _user_tokens(db: Session, user: User) -> list[ApiToken]:
    return list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == user.id)
            .order_by(ApiToken.created_at.desc())
        )
    )


def _render_tokens(
    request: Request, user: User, db: Session, new_token: str | None = None
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_tokens.html",
        {"tokens": _user_tokens(db, user), "new_token": new_token},
    )


@router.post("/app/tokens", response_class=HTMLResponse)
def create_api_token(
    request: Request,
    name: str = Form(...),
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    plain, digest = generate_token()
    db.add(ApiToken(user_id=user.id, name=name.strip() or "unnamed", token_hash=digest))
    db.commit()
    # `plain` rides only in this one response — it is never persisted.
    return _render_tokens(request, user, db, new_token=plain)


@router.delete("/app/tokens/{token_id}", response_class=HTMLResponse)
def revoke_api_token(
    request: Request,
    token_id: uuid.UUID,
    user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return _render_tokens(request, user, db)


# --- SSE live prices (Phase 9) ----------------------------------------------
# Both factories exist as seams so tests can inject sqlite + fakeredis.
def _session() -> Session:
    from core.db import SessionLocal

    return SessionLocal()


def _aioredis() -> "aioredis.Redis":
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _sse_frame(event: str, html: str) -> str:
    data = "\n".join(f"data: {line}" for line in html.splitlines()) or "data:"
    return f"event: {event}\n{data}\n\n"


def _render_offers_html(item_id: uuid.UUID) -> str:
    """Render the offers fragment in its own short-lived session.

    The stream outlives any request-scoped session by minutes; holding a
    Depends(get_db) session open that long would pin a pool connection per
    open tab.
    """
    db = _session()
    try:
        item = db.get(TrackedProduct, item_id)
        if item is None:
            return ""
        return templates.get_template("_offers.html").render(_offers_context(item, db))
    finally:
        db.close()


@router.get("/app/items/{item_id}/stream")
async def item_stream(request: Request, item_id: uuid.UUID):
    # Auth + ownership run in a short-lived session (see _render_offers_html
    # for why not Depends(get_db)). Same 303/404 semantics as the page routes.
    def _gate() -> None:
        db = _session()
        try:
            user = _user_from_cookie(request, db)
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_303_SEE_OTHER,
                    headers={"Location": "/login", "HX-Redirect": "/login"},
                )
            _owned(item_id, user, db)
        finally:
            db.close()

    await anyio.to_thread.run_sync(_gate)

    r = _aioredis()
    pubsub = r.pubsub()
    channel = price_update_channel(str(item_id))
    try:
        await pubsub.subscribe(channel)
    except Exception:  # noqa: BLE001 - Redis down: the page still works statically
        await r.aclose()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live updates are unavailable right now",
        )

    async def event_stream():
        try:
            # Immediate first frame: the client renders without a separate
            # hx-get, and reconnects repaint instantly.
            html = await anyio.to_thread.run_sync(partial(_render_offers_html, item_id))
            yield _sse_frame("offers", html)
            while not await request.is_disconnected():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15.0
                )
                if msg is None:
                    yield ": keep-alive\n\n"  # comment frame; proxies stay open
                    continue
                html = await anyio.to_thread.run_sync(
                    partial(_render_offers_html, item_id)
                )
                yield _sse_frame("offers", html)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await r.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tell buffering proxies to pass frames through
        },
    )
