"""增加店铺问候配置和问候决策审计字段。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260917_0009"
down_revision: Union[str, None] = "20260917_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shop_greeting_configs",
        sa.Column("shop_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("trigger_groups", sa.JSON(), nullable=False),
        sa.Column("reply_templates", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("shop_id"),
    )
    op.add_column(
        "reply_decisions", sa.Column("greeting_type", sa.String(length=32))
    )
    op.add_column(
        "reply_decisions", sa.Column("recognition_source", sa.String(length=16))
    )


def downgrade() -> None:
    op.drop_column("reply_decisions", "recognition_source")
    op.drop_column("reply_decisions", "greeting_type")
    op.drop_table("shop_greeting_configs")
