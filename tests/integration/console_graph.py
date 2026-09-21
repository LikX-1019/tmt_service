"""Shared Agent Graph composition for ConsoleRuntime integration tests."""

from app.agent.dependencies import AgentCapabilities
from app.agent.graph import build_unified_chat_graph
from app.agent.routing import CustomerServiceRouter
from app.agent.runtime import AgentRuntime
from app.agent.service import CustomerServiceAgent
from app.core.config import Settings
from app.repositories.console_repository import ConsoleRepository
from app.repositories.product_repository import ProductRepository
from app.rules.registry import default_rule_registry
from app.services.contextual_fallback_service import ContextualFallbackService
from app.services.product_resolver import ProductResolver
from app.services.product_service import HttpProductClient, ProductAnswerService
from app.services.semantic_product_resolver import SemanticProductResolver
from app.services.social_router import SocialRouter


def build_console_graph_runtime(
    repository: ConsoleRepository,
    qa_provider,
    settings: Settings,
    *,
    agent=None,
    router=None,
    product_provider=None,
    product_answer_service=None,
    social_router=None,
) -> AgentRuntime:
    async def greeting_config_loader(shop_id: str | None):
        if not shop_id:
            return {"enabled": True, "trigger_groups": {}, "reply_templates": {}}
        return await repository.get_greeting_config(shop_id)

    capabilities = AgentCapabilities(
        rules=default_rule_registry(),
        social_router=social_router or SocialRouter(),
        product_resolver=ProductResolver(settings),
        semantic_products=SemanticProductResolver(),
        product_answers=product_answer_service or ProductAnswerService(),
        fallbacks=ContextualFallbackService(),
        qa_provider=qa_provider,
        products=product_provider
        or ProductRepository(HttpProductClient(settings)),
        greeting_agent=agent or CustomerServiceAgent(),
        greeting_config_loader=greeting_config_loader,
        pdd_router=router or CustomerServiceRouter(),
    )
    return AgentRuntime(build_unified_chat_graph(capabilities))
