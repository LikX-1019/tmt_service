"""Agent Graph 的显式能力依赖容器；不承载业务状态或 FastAPI 依赖。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from app.qa.service import QAService
from app.repositories.chat_message_repository import ChatConversationMessageRepository
from app.repositories.conversation_repository import ConversationProductRepository
from app.repositories.product_repository import ProductRepository
from app.rules.registry import RuleRegistry, default_rule_registry
from app.services.contextual_fallback_service import ContextualFallbackService
from app.services.product_resolver import ProductResolver
from app.services.product_service import ProductAnswerService
from app.services.semantic_product_resolver import SemanticProductResolver
from app.services.social_router import SocialRouter


QAProvider = Callable[[], Awaitable[QAService]]


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Unified Chat Graph 所需能力的不可变注入边界。"""

    rules: RuleRegistry
    social_router: SocialRouter
    product_resolver: ProductResolver
    semantic_products: SemanticProductResolver
    product_answers: ProductAnswerService
    fallbacks: ContextualFallbackService
    qa_provider: QAProvider | None = None
    conversations: ConversationProductRepository | None = None
    messages: ChatConversationMessageRepository | None = None
    products: ProductRepository | None = None


def default_capabilities() -> AgentCapabilities:
    """构造无持久化依赖的 Skeleton 能力；显式测试应注入全部所需能力。"""
    return AgentCapabilities(
        rules=default_rule_registry(),
        social_router=SocialRouter(),
        product_resolver=ProductResolver(),
        semantic_products=SemanticProductResolver(),
        product_answers=ProductAnswerService(),
        fallbacks=ContextualFallbackService(),
    )


__all__ = ["AgentCapabilities", "QAProvider", "default_capabilities"]
