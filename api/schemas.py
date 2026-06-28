"""Pydantic schemas: the API's external contract.

Design decision: keep these *separate* from the SQLAlchemy ORM models. The DB
model is our internal storage shape; the schema is what we accept/return over
HTTP. Separating them lets us evolve the DB without breaking clients, and — most
importantly — guarantees we never accidentally serialize a field like
`hashed_password` just because it exists on the row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# --- Auth ---
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # allow building from ORM obj

    id: uuid.UUID
    email: EmailStr
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Wishlist / tracked products ---
class TrackedProductCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=1, max_length=512)
    target_price: Decimal | None = Field(default=None, ge=0)


class TrackedProductUpdate(BaseModel):
    """All fields optional: PATCH-style partial update."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    query: str | None = Field(default=None, min_length=1, max_length=512)
    target_price: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None


class TrackedProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    query: str
    target_price: Decimal | None
    is_active: bool
    created_at: datetime


# --- Offers / price history (Phase 2) ---
class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    source_product_id: str
    title: str
    url: str
    currency: str
    last_price: Decimal | None
    is_available: bool
    last_seen_at: datetime | None


class OffersSummary(BaseModel):
    """Offers for one wishlist item, plus the current best deal among them."""

    offers: list[OfferOut]
    best_offer_id: uuid.UUID | None = None
    best_price: Decimal | None = None


class PricePointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    price: Decimal
    observed_at: datetime


class RefreshAccepted(BaseModel):
    task_id: str
    detail: str = "Refresh enqueued"


class BestDealOut(BaseModel):
    """The best current price for a wishlist item, graded against history."""

    best_offer_id: uuid.UUID | None
    best_price: Decimal | None
    currency: str | None
    lowest_in_window: Decimal | None
    window_days: int
    verdict: str  # below_target | all_time_low | great | fair | no_data
    cached: bool  # was this served from the Redis cache or freshly computed?
