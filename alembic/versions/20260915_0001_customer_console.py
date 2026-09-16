"""创建单店客服控制台数据表。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260915_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cs_qa",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("qa_code", sa.String(100), nullable=False, unique=True),
        sa.Column("product_code", sa.String(50)),
        sa.Column("product_name", sa.String(200)),
        sa.Column("standard_question", sa.String(500), nullable=False),
        sa.Column("standard_answer", sa.Text(), nullable=False),
        sa.Column("similar_questions", sa.JSON()),
        sa.Column("keywords", sa.JSON()),
        sa.Column("intent_code", sa.String(100)),
        sa.Column("question_type", sa.String(50)),
        sa.Column("service_stage", sa.String(20), nullable=False),
        sa.Column("required_points", sa.JSON()),
        sa.Column("prohibited_expressions", sa.JSON()),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("need_human", sa.Boolean(), nullable=False),
        sa.Column("applicable_version", sa.String(100)),
        sa.Column("answer_version", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("source", sa.String(255)),
        sa.Column("import_batch_no", sa.String(64)),
        sa.Column("source_row_no", sa.Integer()),
        sa.Column("effective_at", sa.DateTime()),
        sa.Column("expired_at", sa.DateTime()),
        sa.Column("created_by", sa.String(100)),
        sa.Column("approved_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("service_stage IN ('pre_sale', 'post_sale', 'general')", name="ck_cs_qa_service_stage"),
        sa.CheckConstraint("risk_level IN ('low', 'medium', 'high')", name="ck_cs_qa_risk_level"),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_cs_qa_status"),
        sa.CheckConstraint("review_status IN ('usable', 'pending_review', 'pending_validation')", name="ck_cs_qa_review_status"),
        sa.CheckConstraint("answer_version >= 1", name="ck_cs_qa_answer_version"),
        sa.CheckConstraint("expired_at IS NULL OR effective_at IS NULL OR expired_at > effective_at", name="ck_cs_qa_effective_period"),
    )
    op.create_index("ix_cs_qa_product", "cs_qa", ["product_code"])
    op.create_index("ix_cs_qa_intent", "cs_qa", ["intent_code"])
    op.create_index("ix_cs_qa_type_status", "cs_qa", ["question_type", "status"])
    op.create_index("ix_cs_qa_stage_status", "cs_qa", ["service_stage", "status"])
    op.create_index("ix_cs_qa_review_status", "cs_qa", ["review_status"])
    op.create_index("ix_cs_qa_effective", "cs_qa", ["effective_at", "expired_at"])
    op.create_table(
        "shops",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("global_auto_reply_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shop_id", sa.String(36), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform_conversation_id", sa.String(191), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("goods_id", sa.String(64)),
        sa.Column("goods_name", sa.String(500)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("auto_reply_enabled", sa.Boolean(), nullable=False),
        sa.Column("unread_count", sa.Integer(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("shop_id", "platform_conversation_id", name="uq_conversation_platform"),
    )
    op.create_index("ix_conversations_last_message", "conversations", ["last_message_at"])
    op.create_index("ix_conversations_state", "conversations", ["state"])
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform_message_id", sa.String(191)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_backfill", sa.Boolean(), nullable=False),
        sa.Column("outbound_job_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_messages_fingerprint"),
    )
    op.create_index("ix_messages_conversation_time", "messages", ["conversation_id", "occurred_at"])
    op.create_table(
        "outbound_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_request_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("content", sa.String(400), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("clicked_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_request_id", name="uq_outbound_client_request"),
    )
    op.create_index("ix_outbound_status", "outbound_jobs", ["status"])
    op.create_table(
        "reply_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_key", sa.String(64), nullable=False),
        sa.Column("route", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("qa_code", sa.String(128)),
        sa.Column("top_score", sa.Float()),
        sa.Column("score_margin", sa.Float()),
        sa.Column("risk_reason", sa.String(255)),
        sa.Column("suggested_answer", sa.Text()),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_key", name="uq_reply_decision_batch"),
    )
    op.create_index("ix_reply_decisions_conversation", "reply_decisions", ["conversation_id", "created_at"])
    op.create_table(
        "connector_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_connector_events_event_type", "connector_events", ["event_type"])
    op.create_index("ix_connector_events_created_at", "connector_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("connector_events")
    op.drop_table("reply_decisions")
    op.drop_table("outbound_jobs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("shops")
    op.drop_table("cs_qa")
