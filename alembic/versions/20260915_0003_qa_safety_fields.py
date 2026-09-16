"""为已有 QA 表补齐检索与自动发送安全字段。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260915_0003"
down_revision: Union[str, None] = "20260915_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("cs_qa")
    }


def upgrade() -> None:
    columns = _columns()
    if "retrieval_enabled" not in columns:
        op.add_column(
            "cs_qa",
            sa.Column("retrieval_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.execute(
            "UPDATE cs_qa SET retrieval_enabled = 1 "
            "WHERE status = 'published' AND review_status = 'usable'"
        )
    if "auto_reply_eligible" not in columns:
        op.add_column(
            "cs_qa",
            sa.Column("auto_reply_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "human_required" not in columns:
        op.add_column(
            "cs_qa",
            sa.Column("human_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        if "need_human" in columns:
            op.execute("UPDATE cs_qa SET human_required = need_human")
    if "need_human" in columns:
        op.drop_column("cs_qa", "need_human")


def downgrade() -> None:
    columns = _columns()
    if "need_human" not in columns:
        op.add_column(
            "cs_qa",
            sa.Column("need_human", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        if "human_required" in columns:
            op.execute("UPDATE cs_qa SET need_human = human_required")
    for name in ("human_required", "auto_reply_eligible", "retrieval_enabled"):
        if name in columns:
            op.drop_column("cs_qa", name)
