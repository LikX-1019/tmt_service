"""按稳定顾客、业务日期和回复账号保存客服消息。"""

from datetime import datetime, timezone
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "20260916_0005"
down_revision: Union[str, None] = "20260915_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shop_id", sa.String(36), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform_customer_id", sa.String(191), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("avatar_url", sa.String(1000)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("shop_id", "platform_customer_id", name="uq_customer_platform"),
    )
    op.create_index("ix_customers_shop_name", "customers", ["shop_id", "display_name"])
    op.create_table(
        "agent_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shop_id", sa.String(36), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("account_type", sa.String(32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("shop_id", "display_name", "account_type", name="uq_agent_account_name"),
    )
    op.create_index("ix_agent_accounts_shop", "agent_accounts", ["shop_id", "account_type"])

    op.add_column("conversations", sa.Column("customer_id", sa.String(36)))
    op.create_foreign_key(
        "fk_conversations_customer",
        "conversations",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"], unique=True)

    op.add_column("messages", sa.Column("shop_id", sa.String(36)))
    op.add_column("messages", sa.Column("customer_id", sa.String(36)))
    op.add_column("messages", sa.Column("message_date", sa.Date()))
    op.add_column("messages", sa.Column("sender_type", sa.String(32)))
    op.add_column("messages", sa.Column("sender_account_id", sa.String(36)))
    op.add_column("messages", sa.Column("sender_display_name", sa.String(255)))
    op.add_column(
        "messages",
        sa.Column(
            "assignment_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_foreign_key("fk_messages_shop", "messages", "shops", ["shop_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_messages_customer", "messages", "customers", ["customer_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_messages_agent", "messages", "agent_accounts", ["sender_account_id"], ["id"], ondelete="SET NULL")

    op.add_column("outbound_jobs", sa.Column("responder_account_id", sa.String(36)))
    op.add_column("outbound_jobs", sa.Column("responder_display_name", sa.String(255)))
    op.create_foreign_key(
        "fk_outbound_responder",
        "outbound_jobs",
        "agent_accounts",
        ["responder_account_id"],
        ["id"],
        ondelete="SET NULL",
    )

    bind = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conversations = bind.execute(
        sa.text(
            "SELECT id, shop_id, platform_conversation_id, display_name, avatar_url "
            "FROM conversations"
        )
    ).mappings()
    for row in conversations:
        customer_id = str(uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO customers "
                "(id, shop_id, platform_customer_id, display_name, avatar_url, first_seen_at, last_seen_at, updated_at) "
                "VALUES (:id, :shop_id, :platform_id, :display_name, :avatar_url, :now, :now, :now)"
            ),
            {
                "id": customer_id,
                "shop_id": row["shop_id"],
                "platform_id": f"legacy:{row['platform_conversation_id']}",
                "display_name": row["display_name"],
                "avatar_url": row["avatar_url"],
                "now": now,
            },
        )
        bind.execute(
            sa.text("UPDATE conversations SET customer_id=:customer_id WHERE id=:id"),
            {"customer_id": customer_id, "id": row["id"]},
        )

    bind.execute(
        sa.text(
            "UPDATE messages m JOIN conversations c ON c.id=m.conversation_id "
            "SET m.shop_id=c.shop_id, m.customer_id=c.customer_id, "
            "m.message_date=DATE(DATE_ADD(m.occurred_at, INTERVAL 8 HOUR)), "
            "m.sender_type=CASE WHEN m.direction='inbound' THEN 'customer' ELSE 'agent' END, "
            "m.assignment_verified=0"
        )
    )
    op.create_index("ix_messages_customer_day", "messages", ["customer_id", "message_date", "occurred_at"])
    op.create_index("ix_messages_shop_day", "messages", ["shop_id", "message_date", "occurred_at"])
    op.create_index("ix_messages_agent_day", "messages", ["sender_account_id", "message_date", "occurred_at"])


def downgrade() -> None:
    op.drop_constraint("fk_outbound_responder", "outbound_jobs", type_="foreignkey")
    op.drop_column("outbound_jobs", "responder_display_name")
    op.drop_column("outbound_jobs", "responder_account_id")
    op.drop_index("ix_messages_agent_day", table_name="messages")
    op.drop_index("ix_messages_shop_day", table_name="messages")
    op.drop_index("ix_messages_customer_day", table_name="messages")
    op.drop_constraint("fk_messages_agent", "messages", type_="foreignkey")
    op.drop_constraint("fk_messages_customer", "messages", type_="foreignkey")
    op.drop_constraint("fk_messages_shop", "messages", type_="foreignkey")
    for column in (
        "assignment_verified",
        "sender_display_name",
        "sender_account_id",
        "sender_type",
        "message_date",
        "customer_id",
        "shop_id",
    ):
        op.drop_column("messages", column)
    op.drop_index("ix_conversations_customer_id", table_name="conversations")
    op.drop_constraint("fk_conversations_customer", "conversations", type_="foreignkey")
    op.drop_column("conversations", "customer_id")
    op.drop_table("agent_accounts")
    op.drop_table("customers")
