"""统一 Chat API 的会话商品绑定仓库。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConversationStateUnavailableError, InvalidRequestError
from app.models.chat import ChatConversationProduct


class ConversationProductRepository:
    """只维护 conversation → product 绑定，不创建新的完整会话系统。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _validate_conversation_id(conversation_id: str) -> str:
        normalized = conversation_id.strip()
        if not normalized or len(normalized) > 128:
            raise InvalidRequestError("conversation_id 无效")
        return normalized

    async def get_binding(self, conversation_id: str) -> ChatConversationProduct | None:
        conversation_id = self._validate_conversation_id(conversation_id)
        try:
            async with self._session_factory() as session:
                return await session.get(ChatConversationProduct, conversation_id)
        except SQLAlchemyError as exc:
            raise ConversationStateUnavailableError() from exc

    async def bind_product(
        self,
        conversation_id: str,
        *,
        product_id: str,
        product_name: str,
        recent_products: Sequence[dict[str, str]] | None = None,
    ) -> ChatConversationProduct:
        conversation_id = self._validate_conversation_id(conversation_id)
        product_id = product_id.strip()
        product_name = product_name.strip()
        if not product_id or len(product_id) > 64:
            raise InvalidRequestError("product_id 无效")
        if not product_name or len(product_name) > 500:
            raise InvalidRequestError("product_name 无效")
        normalized_history = self._validate_recent_products(recent_products)
        try:
            async with self._session_factory() as session:
                binding = await session.get(ChatConversationProduct, conversation_id)
                if binding is None:
                    binding = ChatConversationProduct(
                        conversation_id=conversation_id,
                        product_id=product_id,
                        product_name=product_name,
                        recent_products=normalized_history,
                    )
                    session.add(binding)
                else:
                    binding.product_id = product_id
                    binding.product_name = product_name
                    binding.recent_products = normalized_history
                await session.commit()
                await session.refresh(binding)
                return binding
        except SQLAlchemyError as exc:
            raise ConversationStateUnavailableError() from exc

    @staticmethod
    def _validate_recent_products(
        products: Sequence[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        if products is None:
            return []
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for product in list(products)[:5]:
            product_id = str(product.get("product_id", "")).strip()
            product_name = str(product.get("product_name", "")).strip()
            if not product_id or len(product_id) > 64:
                raise InvalidRequestError("历史商品 ID 无效")
            if not product_name or len(product_name) > 500:
                raise InvalidRequestError("历史商品名称无效")
            if product_id in seen:
                continue
            seen.add(product_id)
            normalized.append(
                {"product_id": product_id, "product_name": product_name}
            )
        return normalized

    async def clear_binding(self, conversation_id: str) -> None:
        conversation_id = self._validate_conversation_id(conversation_id)
        try:
            async with self._session_factory() as session:
                binding = await session.get(ChatConversationProduct, conversation_id)
                if binding is not None:
                    await session.delete(binding)
                    await session.commit()
        except SQLAlchemyError as exc:
            raise ConversationStateUnavailableError() from exc
