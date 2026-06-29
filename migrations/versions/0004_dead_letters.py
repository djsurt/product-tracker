"""dead_letters

Revision ID: 0004_dead_letters
Revises: 0003_alerts_clicks
Create Date: 2026-06-29
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_dead_letters"
down_revision = "0003_alerts_clicks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("offer_id", sa.Uuid(), nullable=True),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("retries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_dead_letters_created", "dead_letters", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_dead_letters_created", table_name="dead_letters")
    op.drop_table("dead_letters")
