"""Unified Chat 会话历史的持久化访问。"""

from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConversationStateUnavailableError, InvalidRequestError
from app.models.chat import ChatMessage


class ChatConversationMessageRepository:
    """只保存轻量历史，不做完整客服工作台消息模型。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _validate_conversation_id(conversation_id: str) -> str:
        normalized = conversation_id.strip()
        if not normalized or len(normalized) > 128:
            raise InvalidRequestError("conversation_id 无效")
        return normalized

    @staticmethod
    def _validate_role(role: str) -> str:
        normalized = role.strip().lower()
        if normalized not in {"customer", "assistant"}:
            raise InvalidRequestError("会话消息角色无效")
        return normalized

    async def append(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
    ) -> ChatMessage:
        normalized_conversation_id = self._validate_conversation_id(conversation_id)
        normalized_role = self._validate_role(role)
        normalized_content = content.strip()
        if not normalized_content:
            raise InvalidRequestError("会话消息内容不能为空")
        try:
            async with self._session_factory() as session:
                message = ChatMessage(
                    conversation_id=normalized_conversation_id,
                    role=normalized_role,
                    content=normalized_content,
                )
                session.add(message)
                await session.commit()
                await session.refresh(message)
                return message
        except SQLAlchemyError as exc:
            raise ConversationStateUnavailableError() from exc

    async def append_customer_message(self, conversation_id: str, content: str) -> ChatMessage:
        return await self.append(
            conversation_id,
            role="customer",
            content=content,
        )

    async def append_assistant_message(self, conversation_id: str, content: str) -> ChatMessage:
        return await self.append(
            conversation_id,
            role="assistant",
            content=content,
        )

    async def list_recent_turns(self, conversation_id: str, *, limit: int = 10) -> list[dict[str, str | None]]:
        """按时间正序返回最近 N 组问答；assistant 可能为 None 表示当前用户消息。"""
        normalized_conversation_id = self._validate_conversation_id(conversation_id)
        if not 1 <= limit <= 20:
            raise InvalidRequestError("会话历史轮次数量无效")
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.scalars(
                        select(ChatMessage)
                        .where(ChatMessage.conversation_id == normalized_conversation_id)
                        .order_by(ChatMessage.id.desc(), ChatMessage.created_at.desc())
                        .limit(limit * 2)
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise ConversationStateUnavailableError() from exc

        chronological_messages: list[ChatMessage] = list(reversed(rows))
        turns: list[dict[str, str | None]] = []
        pending_customer: str | None = None
        for message in chronological_messages:
            if message.role == "customer":
                pending_customer = message.content
                continue
            if message.role == "assistant" and pending_customer is not None:
                turns.append(
                    {"customer": pending_customer, "assistant": message.content}
                )
                pending_customer = None
        if pending_customer is not None:
            turns.append({"customer": pending_customer, "assistant": None})
        return turns[-limit:]
