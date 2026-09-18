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

    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("customer_id", sa.String(36)))
        batch.create_foreign_key(
            "fk_conversations_customer",
            "customers",
            ["customer_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_conversations_customer_id", ["customer_id"], unique=True
        )

    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("shop_id", sa.String(36)))
        batch.add_column(sa.Column("customer_id", sa.String(36)))
        batch.add_column(sa.Column("message_date", sa.Date()))
        batch.add_column(sa.Column("sender_type", sa.String(32)))
        batch.add_column(sa.Column("sender_account_id", sa.String(36)))
        batch.add_column(sa.Column("sender_display_name", sa.String(255)))
        batch.add_column(
            sa.Column(
                "assignment_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_foreign_key(
            "fk_messages_shop", "shops", ["shop_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_foreign_key(
            "fk_messages_customer",
            "customers",
            ["customer_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_messages_agent",
            "agent_accounts",
            ["sender_account_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("outbound_jobs") as batch:
        batch.add_column(sa.Column("responder_account_id", sa.String(36)))
        batch.add_column(sa.Column("responder_display_name", sa.String(255)))
        batch.create_foreign_key(
            "fk_outbound_responder",
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

    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                "UPDATE messages SET "
                "shop_id=(SELECT c.shop_id FROM conversations c WHERE c.id=messages.conversation_id), "
                "customer_id=(SELECT c.customer_id FROM conversations c WHERE c.id=messages.conversation_id), "
                "message_date=DATE(datetime(occurred_at, '+8 hours')), "
                "sender_type=CASE WHEN direction='inbound' THEN 'customer' ELSE 'agent' END, "
                "assignment_verified=0"
            )
        )
    else:
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
    with op.batch_alter_table("outbound_jobs") as batch:
        batch.drop_constraint("fk_outbound_responder", type_="foreignkey")
        batch.drop_column("responder_display_name")
        batch.drop_column("responder_account_id")
    op.drop_index("ix_messages_agent_day", table_name="messages")
    op.drop_index("ix_messages_shop_day", table_name="messages")
    op.drop_index("ix_messages_customer_day", table_name="messages")
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("fk_messages_agent", type_="foreignkey")
        batch.drop_constraint("fk_messages_customer", type_="foreignkey")
        batch.drop_constraint("fk_messages_shop", type_="foreignkey")
        for column in (
            "assignment_verified",
            "sender_display_name",
            "sender_account_id",
            "sender_type",
            "message_date",
            "customer_id",
            "shop_id",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("conversations") as batch:
        batch.drop_index("ix_conversations_customer_id")
        batch.drop_constraint("fk_conversations_customer", type_="foreignkey")
        batch.drop_column("customer_id")
    op.drop_table("agent_accounts")
    op.drop_table("customers")
