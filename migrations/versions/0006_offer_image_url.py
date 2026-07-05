"""Add offers.image_url — product image per offer, for the storefront UI.

Revision ID: 0006_offer_image_url
Revises: 0005_api_tokens
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_offer_image_url"
down_revision = "0005_api_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("image_url", sa.String(1024), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "image_url")
