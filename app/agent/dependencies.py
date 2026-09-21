"""Agent Graph 的显式能力依赖容器；不承载业务状态或 FastAPI 依赖。"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any



def default_rule_registry() -> RuleRegistry:
    """延迟导入，避免 rules → agent.state → agent graph → rules 循环。"""
    from app.rules.registry import default_rule_registry

    return default_rule_registry()


QAProvider = Callable[[], Awaitable[Any]]
RuleRegistry = Any


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Unified Chat Graph 所需能力的不可变注入边界。"""

    rules: Any
    social_router: Any
    product_resolver: Any
    semantic_products: Any
    product_answers: Any
    fallbacks: Any
    qa_provider: QAProvider | None = None
    conversations: Any | None = None
    messages: Any | None = None
    products: Any | None = None


def default_capabilities() -> AgentCapabilities:
    """延迟导入能力实现，避免 rules/services → agent graph → rules 循环。"""
    from app.rules.registry import default_rule_registry
    from app.services.contextual_fallback_service import ContextualFallbackService
    from app.services.product_resolver import ProductResolver
    from app.services.product_service import ProductAnswerService
    from app.services.semantic_product_resolver import SemanticProductResolver
    from app.services.social_router import SocialRouter

    return AgentCapabilities(
        rules=default_rule_registry(),
        social_router=SocialRouter(),
        product_resolver=ProductResolver(),
        semantic_products=SemanticProductResolver(),
        product_answers=ProductAnswerService(),
        fallbacks=ContextualFallbackService(),
    )


__all__ = ["AgentCapabilities", "QAProvider", "default_capabilities"]
