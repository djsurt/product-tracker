"""offers + price_history

Revision ID: 0002_offers_price_history
Revises: 0001_initial
Create Date: 2026-06-27
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_offers_price_history"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "offers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tracked_product_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_product_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("last_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("is_available", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tracked_product_id"], ["tracked_products.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "tracked_product_id",
            "source",
            "source_product_id",
            name="uq_offer_identity",
        ),
    )
    op.create_index("ix_offers_tracked_product_id", "offers", ["tracked_product_id"])

    op.create_table(
        "price_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("observed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_price_history_offer_observed", "price_history", ["offer_id", "observed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_price_history_offer_observed", table_name="price_history")
    op.drop_table("price_history")
    op.drop_index("ix_offers_tracked_product_id", table_name="offers")
    op.drop_table("offers")
