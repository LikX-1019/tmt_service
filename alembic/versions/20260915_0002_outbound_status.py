"""在会话列表记录最近一次发送状态。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260915_0002"
down_revision: Union[str, None] = "20260915_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("last_outbound_status", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "last_outbound_status")
