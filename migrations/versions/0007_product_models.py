"""Add product_models — AI-generated 3D/AR previews (Phase 10).

Revision ID: 0007_product_models
Revises: 0006_offer_image_url
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_product_models"
down_revision = "0006_offer_image_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_models",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tracked_product_id",
            sa.Uuid(),
            sa.ForeignKey("tracked_products.id", ondelete="CASCADE"),
            unique=True,
            index=True,
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(20), nullable=False, server_default="meshy"),
        sa.Column("provider_task_id", sa.String(255), nullable=True),
        sa.Column("source_image_url", sa.String(1024), nullable=False),
        sa.Column("glb_path", sa.String(1024), nullable=True),
        sa.Column("usdz_path", sa.String(1024), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("product_models")
