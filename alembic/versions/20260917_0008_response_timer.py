"""为待回复会话保存可配置倒计时的开始时间和截止时间。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260917_0008"
down_revision: Union[str, None] = "20260916_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations", sa.Column("response_started_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "conversations", sa.Column("response_deadline_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "ix_conversations_response_deadline",
        "conversations",
        ["response_deadline_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_response_deadline", table_name="conversations")
    op.drop_column("conversations", "response_deadline_at")
    op.drop_column("conversations", "response_started_at")
