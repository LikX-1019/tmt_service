"""单店客服控制台的持久化模型。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.agent.greeting import default_reply_templates, default_trigger_groups
from app.models.base import Base


def _uuid() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Shop(Base):
    """已接入的平台店铺；首版只创建一个默认店铺。"""

    __tablename__ = "shops"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(32), default="pdd", nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="拼多多店铺", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    global_auto_reply_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    customers: Mapped[list[Customer]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    agent_accounts: Mapped[list[AgentAccount]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )


class ShopGreetingConfig(Base):
    """店铺级一般问候触发语和固定回复配置。"""

    __tablename__ = "shop_greeting_configs"

    shop_id: Mapped[str] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trigger_groups: Mapped[dict[str, list[str]]] = mapped_column(
        JSON, default=default_trigger_groups, nullable=False
    )
    reply_templates: Mapped[dict[str, list[str]]] = mapped_column(
        JSON, default=default_reply_templates, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Customer(Base):
    """平台稳定顾客实体；昵称和头像是可变的当前资料。"""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint(
            "shop_id", "platform_customer_id", name="uq_customer_platform"
        ),
        Index("ix_customers_shop_name", "shop_id", "display_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shop_id: Mapped[str] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    platform_customer_id: Mapped[str] = mapped_column(String(191), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    shop: Mapped[Shop] = relationship(back_populates="customers")


class AgentAccount(Base):
    """从拼多多消息气泡识别出的人工或机器人回复账号。"""

    __tablename__ = "agent_accounts"
    __table_args__ = (
        UniqueConstraint(
            "shop_id", "display_name", "account_type", name="uq_agent_account_name"
        ),
        Index("ix_agent_accounts_shop", "shop_id", "account_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shop_id: Mapped[str] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    shop: Mapped[Shop] = relationship(back_populates="agent_accounts")


class Conversation(Base):
    """平台会话及其人工接管、商品上下文和未读状态。"""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "shop_id", "platform_conversation_id", name="uq_conversation_platform"
        ),
        Index("ix_conversations_last_message", "last_message_at"),
        Index("ix_conversations_response_deadline", "response_deadline_at"),
        Index("ix_conversations_state", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shop_id: Mapped[str] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    platform_conversation_id: Mapped[str] = mapped_column(String(191), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), default="顾客", nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    goods_id: Mapped[str | None] = mapped_column(String(64))
    goods_name: Mapped[str | None] = mapped_column(String(500))
    goods_price: Mapped[str | None] = mapped_column(String(64))
    goods_url: Mapped[str | None] = mapped_column(String(2000))
    state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    last_outbound_status: Mapped[str | None] = mapped_column(String(16))
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    shop: Mapped[Shop] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """去重后的收发消息；浏览器回填历史消息会标记为 backfill。"""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_messages_fingerprint"),
        Index("ix_messages_conversation_time", "conversation_id", "occurred_at"),
        Index("ix_messages_customer_day", "customer_id", "message_date", "occurred_at"),
        Index("ix_messages_shop_day", "shop_id", "message_date", "occurred_at"),
        Index("ix_messages_agent_day", "sender_account_id", "message_date", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    shop_id: Mapped[str | None] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE")
    )
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    platform_message_id: Mapped[str | None] = mapped_column(String(191))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message_date: Mapped[date | None] = mapped_column(Date)
    sender_type: Mapped[str | None] = mapped_column(String(32))
    sender_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_accounts.id", ondelete="SET NULL")
    )
    sender_display_name: Mapped[str | None] = mapped_column(String(255))
    assignment_verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_backfill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    outbound_job_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    assets: Mapped[list["MessageAsset"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", lazy="selectin"
    )


class MessageAsset(Base):
    """消息关联的本地媒体资源；source_url 仅用于审计与重试。"""

    __tablename__ = "message_assets"
    __table_args__ = (
        Index("ix_message_assets_message", "message_id"),
        Index("ix_message_assets_sha256", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(4000))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    sha256: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    message: Mapped[Message] = relationship(back_populates="assets")


class OutboundJob(Base):
    """发送任务及可审计状态；uncertain 任务不会自动重试。"""

    __tablename__ = "outbound_jobs"
    __table_args__ = (
        UniqueConstraint("client_request_id", name="uq_outbound_client_request"),
        Index("ix_outbound_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    client_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(String(400), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    responder_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_accounts.id", ondelete="SET NULL")
    )
    responder_display_name: Mapped[str | None] = mapped_column(String(255))
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ReplyDecision(Base):
    """每批入站消息的自动回复决策和脱敏证据。"""

    __tablename__ = "reply_decisions"
    __table_args__ = (
        UniqueConstraint("batch_key", name="uq_reply_decision_batch"),
        Index("ix_reply_decisions_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    batch_key: Mapped[str] = mapped_column(String(64), nullable=False)
    route: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    greeting_type: Mapped[str | None] = mapped_column(String(32))
    recognition_source: Mapped[str | None] = mapped_column(String(16))
    qa_code: Mapped[str | None] = mapped_column(String(128))
    top_score: Mapped[float | None] = mapped_column(Float)
    score_margin: Mapped[float | None] = mapped_column(Float)
    risk_reason: Mapped[str | None] = mapped_column(String(255))
    suggested_answer: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class ConnectorEvent(Base):
    """SSE 可恢复事件；正文只放控制台必要的结构化字段。"""

    __tablename__ = "connector_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
