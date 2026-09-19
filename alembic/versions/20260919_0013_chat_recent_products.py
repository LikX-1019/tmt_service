"""add recent product history to chat bindings

Revision ID: 20260919_0013
Revises: 20260918_0012
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260919_0013"
down_revision = "20260918_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL does not allow server defaults for JSON columns. Add first, backfill,
    # then enforce NOT NULL so both fresh and existing databases converge safely.
    op.add_column(
        "chat_conversation_products",
        sa.Column("recent_products", sa.JSON(), nullable=True),
    )
    op.execute(
        "UPDATE chat_conversation_products "
        "SET recent_products = '[]' WHERE recent_products IS NULL"
    )
    op.alter_column(
        "chat_conversation_products",
        "recent_products",
        existing_type=sa.JSON(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("chat_conversation_products", "recent_products")
