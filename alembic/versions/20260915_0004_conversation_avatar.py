"""为客服会话保存顾客头像。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260915_0004"
down_revision: Union[str, None] = "20260915_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("conversations")
    }
    if "avatar_url" not in columns:
        op.add_column("conversations", sa.Column("avatar_url", sa.String(1000)))


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("conversations")
    }
    if "avatar_url" in columns:
        op.drop_column("conversations", "avatar_url")
