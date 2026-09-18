"""增加人工接管配置和商品路由审计字段。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260917_0010"
down_revision: Union[str, None] = "20260917_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shop_handoff_configs",
        sa.Column("shop_id", sa.String(length=36), nullable=False),
        sa.Column("reply_template", sa.String(length=400), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("shop_id"),
    )
    op.add_column("reply_decisions", sa.Column("product_id", sa.String(length=64)))
    op.add_column("reply_decisions", sa.Column("product_name", sa.String(length=500)))


def downgrade() -> None:
    op.drop_column("reply_decisions", "product_name")
    op.drop_column("reply_decisions", "product_id")
    op.drop_table("shop_handoff_configs")
