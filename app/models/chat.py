"""统一 Chat API 的持久化上下文模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChatConversationProduct(Base):
    """Chat conversation 当前绑定的 PostgreSQL 商品引用。"""

    __tablename__ = "chat_conversation_products"

    conversation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
