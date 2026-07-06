"""Add offers.is_delisted — listing permanently gone at the source (404/410).

Separates "stop fetching this forever" from is_available=False, which only
means the last price can't be trusted until a fetch succeeds again.

Revision ID: 0008_offer_is_delisted
Revises: 0007_product_models
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_offer_is_delisted"
down_revision = "0007_product_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("is_delisted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("offers", "is_delisted")
