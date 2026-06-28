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
    Numeric,
    String,
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
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Denormalized "latest" snapshot for cheap reads. The authoritative log is
    # price_history; these columns just save a query for the common case.
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
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
