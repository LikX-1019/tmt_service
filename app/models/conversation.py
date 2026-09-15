"""单店客服控制台的持久化模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class Conversation(Base):
    """平台会话及其人工接管、商品上下文和未读状态。"""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "shop_id", "platform_conversation_id", name="uq_conversation_platform"
        ),
        Index("ix_conversations_last_message", "last_message_at"),
        Index("ix_conversations_state", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shop_id: Mapped[str] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    platform_conversation_id: Mapped[str] = mapped_column(String(191), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="顾客", nullable=False)
    goods_id: Mapped[str | None] = mapped_column(String(64))
    goods_name: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    last_outbound_status: Mapped[str | None] = mapped_column(String(16))
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    platform_message_id: Mapped[str | None] = mapped_column(String(191))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_backfill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    outbound_job_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


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
