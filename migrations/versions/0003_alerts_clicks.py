"""alerts + click_events

Revision ID: 0003_alerts_clicks
Revises: 0002_offers_price_history
Create Date: 2026-06-28
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_alerts_clicks"
down_revision = "0002_offers_price_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tracked_product_id", sa.Uuid(), nullable=False),
        sa.Column("rule", sa.String(length=50), nullable=False),
        sa.Column("threshold", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("channel", sa.String(length=50), server_default="email", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tracked_product_id"], ["tracked_products.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_alerts_user_id", "alerts", ["user_id"])
    op.create_index(
        "ix_alerts_tracked_product_id", "alerts", ["tracked_product_id"]
    )

    op.create_table(
        "click_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("clicked_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_click_events_offer_clicked", "click_events", ["offer_id", "clicked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_click_events_offer_clicked", table_name="click_events")
    op.drop_table("click_events")
    op.drop_index("ix_alerts_tracked_product_id", table_name="alerts")
    op.drop_index("ix_alerts_user_id", table_name="alerts")
    op.drop_table("alerts")
