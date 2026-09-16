"""保存消息图片、表情、商品图与兜底截图。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260916_0006"
down_revision: Union[str, None] = "20260916_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("goods_price", sa.String(64)))
    op.add_column("conversations", sa.Column("goods_url", sa.String(2000)))
    op.create_table(
        "message_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("source_url", sa.String(4000)),
        sa.Column("storage_key", sa.String(500)),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("metadata_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_message_assets_message", "message_assets", ["message_id"])
    op.create_index("ix_message_assets_sha256", "message_assets", ["sha256"])


def downgrade() -> None:
    op.drop_column("conversations", "goods_url")
    op.drop_column("conversations", "goods_price")
    op.drop_index("ix_message_assets_sha256", table_name="message_assets")
    op.drop_index("ix_message_assets_message", table_name="message_assets")
    op.drop_table("message_assets")
