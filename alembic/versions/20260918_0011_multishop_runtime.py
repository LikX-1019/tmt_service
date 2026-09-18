"""Add isolated multi-shop browser runtimes and operations data."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260918_0011"
down_revision: Union[str, None] = "20260917_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shops") as batch:
        batch.add_column(sa.Column("platform_shop_id", sa.String(191)))
        batch.add_column(
            sa.Column(
                "lifecycle_status",
                sa.String(24),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(
            sa.Column(
                "desired_online",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("browser_profile_key", sa.String(64)))
        batch.add_column(
            sa.Column(
                "reception_mode",
                sa.String(24),
                nullable=False,
                server_default="assist",
            )
        )
        batch.add_column(sa.Column("identified_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_error_code", sa.String(64)))

    connection = op.get_bind()
    shops = sa.table(
        "shops",
        sa.column("id", sa.String),
        sa.column("browser_profile_key", sa.String),
        sa.column("reception_mode", sa.String),
        sa.column("global_auto_reply_enabled", sa.Boolean),
    )
    rows = list(
        connection.execute(
            sa.select(shops.c.id, shops.c.global_auto_reply_enabled).order_by(shops.c.id)
        )
    )
    for index, row in enumerate(rows):
        connection.execute(
            shops.update()
            .where(shops.c.id == row.id)
            .values(
                browser_profile_key="legacy" if index == 0 else f"shop-{row.id}",
                reception_mode=(
                    "guarded_auto" if row.global_auto_reply_enabled else "assist"
                ),
            )
        )

    with op.batch_alter_table("shops") as batch:
        batch.alter_column("browser_profile_key", nullable=False)
        batch.create_unique_constraint(
            "uq_shop_platform_id", ["platform", "platform_shop_id"]
        )
        batch.create_unique_constraint("uq_shop_profile_key", ["browser_profile_key"])
        batch.create_index(
            "ix_shops_lifecycle_online", ["lifecycle_status", "desired_online"]
        )

    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("uq_messages_fingerprint", type_="unique")
        batch.alter_column("shop_id", existing_type=sa.String(36), nullable=False)
        batch.create_unique_constraint(
            "uq_messages_shop_fingerprint", ["shop_id", "fingerprint"]
        )

    with op.batch_alter_table("outbound_jobs") as batch:
        batch.add_column(sa.Column("shop_id", sa.String(36)))
        batch.add_column(sa.Column("response_deadline_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE outbound_jobs SET shop_id = "
        "(SELECT conversations.shop_id FROM conversations "
        "WHERE conversations.id = outbound_jobs.conversation_id)"
    )
    with op.batch_alter_table("outbound_jobs") as batch:
        batch.drop_constraint("uq_outbound_client_request", type_="unique")
        batch.alter_column("shop_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key(
            "fk_outbound_jobs_shop", "shops", ["shop_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_unique_constraint(
            "uq_outbound_shop_client_request", ["shop_id", "client_request_id"]
        )

    with op.batch_alter_table("reply_decisions") as batch:
        batch.add_column(sa.Column("shop_id", sa.String(36)))
    op.execute(
        "UPDATE reply_decisions SET shop_id = "
        "(SELECT conversations.shop_id FROM conversations "
        "WHERE conversations.id = reply_decisions.conversation_id)"
    )
    with op.batch_alter_table("reply_decisions") as batch:
        batch.drop_constraint("uq_reply_decision_batch", type_="unique")
        batch.alter_column("shop_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key(
            "fk_reply_decisions_shop", "shops", ["shop_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_unique_constraint(
            "uq_reply_decision_shop_batch", ["shop_id", "batch_key"]
        )

    with op.batch_alter_table("connector_events") as batch:
        batch.add_column(sa.Column("shop_id", sa.String(36)))
        batch.create_foreign_key(
            "fk_connector_events_shop", "shops", ["shop_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index("ix_connector_events_shop_id", ["shop_id"])

    op.create_table(
        "customer_notes",
        sa.Column(
            "customer_id",
            sa.String(36),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "shop_id",
            sa.String(36),
            sa.ForeignKey("shops.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_customer_notes_shop_id", "customer_notes", ["shop_id"])

    op.create_table(
        "knowledge_gaps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "shop_id",
            sa.String(36),
            sa.ForeignKey("shops.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("normalized_question", sa.String(500), nullable=False),
        sa.Column("example_question", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("candidate_answer", sa.Text()),
        sa.Column("linked_qa_code", sa.String(100)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "shop_id",
            "product_id",
            "normalized_question",
            "reason_code",
            name="uq_knowledge_gap_scope",
        ),
    )
    op.create_index(
        "ix_knowledge_gaps_shop_status",
        "knowledge_gaps",
        ["shop_id", "status", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_table("knowledge_gaps")
    op.drop_table("customer_notes")
    with op.batch_alter_table("connector_events") as batch:
        batch.drop_index("ix_connector_events_shop_id")
        batch.drop_constraint("fk_connector_events_shop", type_="foreignkey")
        batch.drop_column("shop_id")
    with op.batch_alter_table("reply_decisions") as batch:
        batch.drop_constraint("uq_reply_decision_shop_batch", type_="unique")
        batch.create_unique_constraint("uq_reply_decision_batch", ["batch_key"])
        batch.drop_constraint("fk_reply_decisions_shop", type_="foreignkey")
        batch.drop_column("shop_id")
    with op.batch_alter_table("outbound_jobs") as batch:
        batch.drop_constraint("uq_outbound_shop_client_request", type_="unique")
        batch.create_unique_constraint("uq_outbound_client_request", ["client_request_id"])
        batch.drop_constraint("fk_outbound_jobs_shop", type_="foreignkey")
        batch.drop_column("response_deadline_at")
        batch.drop_column("shop_id")
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("uq_messages_shop_fingerprint", type_="unique")
        batch.create_unique_constraint("uq_messages_fingerprint", ["fingerprint"])
        batch.alter_column("shop_id", existing_type=sa.String(36), nullable=True)
    with op.batch_alter_table("shops") as batch:
        batch.drop_index("ix_shops_lifecycle_online")
        batch.drop_constraint("uq_shop_profile_key", type_="unique")
        batch.drop_constraint("uq_shop_platform_id", type_="unique")
        for column in (
            "last_error_code",
            "identified_at",
            "reception_mode",
            "browser_profile_key",
            "desired_online",
            "lifecycle_status",
            "platform_shop_id",
        ):
            batch.drop_column(column)
