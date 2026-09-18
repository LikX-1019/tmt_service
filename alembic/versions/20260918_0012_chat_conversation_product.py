"""add chat conversation product bindings

Revision ID: 20260918_0012
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260918_0012"
down_revision = "20260918_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_conversation_products",
        sa.Column("conversation_id", sa.String(length=128), primary_key=True),
        sa.Column("product_id", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_conversation_products")
