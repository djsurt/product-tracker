"""Outbound redirect: GET /go/{offer_id} (Phase 4).

This is the link we put in alert emails. It is intentionally **unauthenticated**
— it's followed straight from an email client with no session — so it must not
expose anything sensitive; it only logs the click and 302s to the offer's
destination URL.

Keeping the redirect on our own domain (instead of emailing the raw store URL)
means our app stays the source of truth for "which deals did users act on": the
click log is the seam where analytics and, later, affiliate tracking params
would attach.
"""

import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from core import metrics
from core.db import get_db
from core.models import ClickEvent, Offer

router = APIRouter(prefix="/go", tags=["redirect"])


@router.get("/{offer_id}")
def go(offer_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    offer = db.get(Offer, offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # offer.url comes from external sources (scraped hrefs, API payloads), so we
    # don't trust it blindly: only ever 302 to a real http(s) destination. This
    # rejects empty URLs and dangerous schemes (javascript:, data:, file:) that
    # would otherwise turn this trusted link into an XSS/phishing vector.
    if urlparse(offer.url).scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Offer has no valid destination"
        )

    db.add(ClickEvent(offer_id=offer.id))
    db.commit()
    metrics.inc("deal_redirect_click_total")

    # 302 (temporary): the destination for a given offer can change over time as
    # the source's URL changes, so we don't want clients to cache it permanently.
    return RedirectResponse(url=offer.url, status_code=status.HTTP_302_FOUND)
