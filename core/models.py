"""ORM models.

Design decisions:
- **UUID primary keys** (not auto-increment ints). UUIDs don't leak how many
  users/items exist and don't collide if we ever generate IDs across multiple
  services — both relevant for a system-design-focused project. SQLAlchemy's
  generic `Uuid` type maps to native `uuid` on Postgres and to a 32-char string
  on SQLite, so the same models work in prod and in tests.
- **Server-side timestamps** via `func.now()` so the database, not the app
  clock, stamps rows — consistent even across multiple app instances.
- **Numeric for money**, never float. Floats can't represent 19.99 exactly;
  Numeric/Decimal avoids rounding bugs in price comparisons.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # One user has many wishlist items. `cascade` deletes their items if the
    # user is deleted; `lazy="selectin"` avoids N+1 queries when loading them.
    tracked_products: Mapped[list[TrackedProduct]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TrackedProduct(Base):
    """A single wishlist item: "watch the price of <query> for me"."""

    __tablename__ = "tracked_products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(255))
    # What we actually search sources for: a search term, UPC, or model number.
    query: Mapped[str] = mapped_column(String(512))
    # Optional: alert me when the price drops below this. Nullable.
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped[User] = relationship(back_populates="tracked_products")
    offers: Mapped[list[Offer]] = relationship(
        back_populates="tracked_product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    alerts: Mapped[list[Alert]] = relationship(
        back_populates="tracked_product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Offer(Base):
    """One product, from one source. A tracked product fans out to many offers
    (eBay, Best Buy, mock store, ...), and we pick the best price among them.
    """

    __tablename__ = "offers"
    __table_args__ = (
        # Discovery is idempotent: re-running it must not create duplicate offers
        # for the same (product, source, source-side id).
        UniqueConstraint(
            "tracked_product_id",
            "source",
            "source_product_id",
            name="uq_offer_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tracked_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tracked_products.id", ondelete="CASCADE"), index=True
    )

    source: Mapped[str] = mapped_column(String(50))          # e.g. "mock", "ebay"
    source_product_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1024))
    # Product image from the source (API field or page structured data), for
    # the storefront feel. Nullable: not every source/page provides one.
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Denormalized "latest" snapshot for cheap reads. The authoritative log is
    # price_history; these columns just save a query for the common case.
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    # Permanently gone at the source (404/410 on fetch): the sweep stops
    # refreshing it. Distinct from is_available=False, which only means "last
    # price untrusted" and is revived by the next successful fetch.
    is_delisted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    tracked_product: Mapped[TrackedProduct] = relationship(back_populates="offers")
    price_points: Mapped[list[PricePoint]] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )


class PricePoint(Base):
    """Append-only time-series: one row per observed price. We never UPDATE these
    rows — that immutability is what lets us answer "lowest price in the last N
    days" and chart history (real best-deal logic arrives in Phase 3).
    """

    __tablename__ = "price_history"
    __table_args__ = (
        Index("ix_price_history_offer_observed", "offer_id", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE")
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    observed_at: Mapped[datetime] = mapped_column(server_default=func.now())

    offer: Mapped[Offer] = relationship(back_populates="price_points")


class Alert(Base):
    """A user's rule for when to be notified about a wishlist item (Phase 4).

    Keeping alerts as their own rows — rather than reusing the product's
    `target_price` — lets one product carry several independent rules
    ("below $300" AND "any 10% drop") and gives us a place to debounce each one
    via `last_fired_at`. The notify worker reads these rows to decide whether a
    `price_dropped` event should actually reach the user.
    """

    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    tracked_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tracked_products.id", ondelete="CASCADE"), index=True
    )

    # "below_target" (best price <= threshold) or "pct_drop" (offer fell >= N%).
    rule: Mapped[str] = mapped_column(String(50))
    # For below_target this is a price; for pct_drop a percentage (e.g. 10).
    # Nullable so a below_target alert can fall back to the product's target_price.
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    channel: Mapped[str] = mapped_column(String(50), default="email")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Debounce anchor: we won't re-fire the same alert within a cooldown window.
    last_fired_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    tracked_product: Mapped[TrackedProduct] = relationship(back_populates="alerts")


class ClickEvent(Base):
    """An outbound click through our /go/{offer_id} redirect (Phase 4).

    Logging the click before redirecting keeps our app the source of truth for
    "which deals did users actually act on" — the hook future affiliate
    attribution would hang off. The redirect link is followed straight from an
    email, so there's no authenticated user; we just record the offer + time.
    """

    __tablename__ = "click_events"
    __table_args__ = (
        Index("ix_click_events_offer_clicked", "offer_id", "clicked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE")
    )
    clicked_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DeadLetter(Base):
    """A task that failed permanently, after exhausting its retries (Phase 5).

    The reliability lesson: a job that keeps failing shouldn't retry forever or
    vanish silently. We park a record here (the "dead-letter queue" pattern) so
    a human can inspect what broke without it blocking the pipeline, and we mark
    the offer stale so its bad price isn't trusted. `offer_id` is nullable so we
    can still record failures that aren't tied to a surviving offer row.
    """

    __tablename__ = "dead_letters"
    __table_args__ = (Index("ix_dead_letters_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), nullable=True
    )
    task_name: Mapped[str] = mapped_column(String(255))
    error: Mapped[str] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ApiToken(Base):
    """A personal access token for the remote MCP endpoint (Phase 8).

    We store only a SHA-256 digest of the token — the plaintext is shown once
    at creation and never again (the GitHub PAT pattern), so a DB leak doesn't
    hand out working credentials. `last_used_at` lets the UI show whether a
    token is actually in use before the user revokes it.
    """

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ProductModel3D(Base):
    """AI-generated 3D preview for a tracked product (Phase 10).

    One row per product (unique FK) — that row IS the cache-once guarantee.
    Model files live on disk under MODEL3D_STORAGE_DIR; rows hold paths only.
    """

    __tablename__ = "product_models"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tracked_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tracked_products.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider: Mapped[str] = mapped_column(String(20), default="meshy")
    provider_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_image_url: Mapped[str] = mapped_column(String(1024))
    glb_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    usdz_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
