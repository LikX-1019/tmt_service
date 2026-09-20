"""FastAPI 依赖提供模块，集中管理接口所需服务实例。"""

from __future__ import annotations

import asyncio
from functools import lru_cache

from fastapi import Request

from app.agent.chat_runtime import ChatStateRuntime
from app.agent.checkpoint import FileCheckpointStore
from app.agent.coordinator import StateCoordinator
from app.agent.dependencies import AgentCapabilities
from app.agent.graph import build_unified_chat_graph
from app.agent.runtime import AgentRuntime
from app.core.config import get_settings
from app.database.session import get_session_factory
from app.core.exceptions import ConsoleUnavailableError
from app.qa.answer_generator import QAAnswerGenerator
from app.qa.catalog import load_catalog
from app.qa.evidence_checker import EvidenceChecker
from app.qa.faq_matcher import FAQMatcher
from app.qa.service import QAService
from app.rag.retrieval.bm25_retriever import BM25Retriever
from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.reranker import BGEReranker
from app.rules.registry import default_rule_registry
from app.repositories.chat_message_repository import ChatConversationMessageRepository
from app.repositories.conversation_repository import ConversationProductRepository
from app.repositories.product_repository import ProductRepository
from app.services.chat_service import ChatService
from app.services.contextual_fallback_service import ContextualFallbackService
from app.services.console_runtime import ConsoleRuntime
from app.services.product_resolver import ProductResolver
from app.services.product_service import ProductAnswerService
from app.services.semantic_product_resolver import SemanticProductResolver
from app.services.shop_runtime_manager import ShopRuntimeManager
from app.services.social_router import SocialRouter

_qa_service: QAService | None = None
_qa_service_lock = asyncio.Lock()


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    """返回统一后端聊天路由；懒加载商品、会话和 QA 依赖。"""
    conversations = ConversationProductRepository(get_session_factory())
    messages = ChatConversationMessageRepository(get_session_factory())
    products = ProductRepository()
    fallback_service = ContextualFallbackService()
    rules = default_rule_registry()
    social_router = SocialRouter()
    product_resolver = ProductResolver()
    semantic_products = SemanticProductResolver()
    checkpoint_store = FileCheckpointStore()
    coordinator = StateCoordinator(checkpoint_store)
    capabilities = AgentCapabilities(
        rules=rules,
        social_router=social_router,
        product_resolver=product_resolver,
        semantic_products=semantic_products,
        product_answers=ProductAnswerService(),
        fallbacks=fallback_service,
        qa_provider=get_qa_service,
        conversations=conversations,
        messages=messages,
        products=products,
    )
    return ChatService(
        conversation_repository=conversations,
        message_repository=messages,
        product_repository=products,
        fallback_service=fallback_service,
        qa_provider=get_qa_service,
        state_runtime=ChatStateRuntime(
            checkpoint_store,
            coordinator=coordinator,
            lifecycle_mode=get_settings().unified_chat_runtime,
        ),
        agent_runtime=AgentRuntime(
            build_unified_chat_graph(capabilities, coordinator=coordinator),
            coordinator=coordinator,
        ),
        runtime_mode=get_settings().unified_chat_runtime,
    )


async def get_qa_service() -> QAService:
    """加载一次数据库目录并复用 FAQ/BM25 索引。"""
    global _qa_service
    if _qa_service is not None:
        return _qa_service
    async with _qa_service_lock:
        if _qa_service is None:
            settings = get_settings()
            catalog = await load_catalog(settings)
            dense = DenseRetriever(settings=settings)
            bm25 = BM25Retriever(catalog.documents, top_k=settings.rag_bm25_top_k)
            retriever = HybridRetriever(
                dense,
                bm25,
                rrf_k=settings.rrf_k,
                top_k=settings.rag_fusion_top_k,
            )
            _qa_service = QAService(
                FAQMatcher(catalog.faq_items),
                retriever,
                BGEReranker(settings=settings),
                EvidenceChecker(settings.rag_score_threshold),
                QAAnswerGenerator(),
                has_knowledge=bool(catalog.documents),
            )
    return _qa_service


def get_shop_runtime_manager(
    request: Request,
) -> ShopRuntimeManager | _LegacyRuntimeManager:
    """返回应用生命周期持有的多店运行时管理器。"""
    manager = getattr(request.app.state, "shop_runtime_manager", None)
    if manager is not None:
        return manager
    # 测试和旧嵌入方可继续注入单店 runtime。
    runtime = getattr(request.app.state, "console_runtime", None)
    if runtime is not None:
        return _LegacyRuntimeManager(runtime)
    if getattr(request.app.state, "console_enabled", True) is False:
        raise ConsoleUnavailableError("客服控制台未启用")
    raise RuntimeError("店铺运行时尚未初始化")


async def get_console_runtime(request: Request) -> ConsoleRuntime:
    """旧无作用域接口：只有唯一 active 店铺时才允许推断。"""
    manager = get_shop_runtime_manager(request)
    if isinstance(manager, _LegacyRuntimeManager):
        return manager.runtime
    return await manager.legacy_runtime()


class _LegacyRuntimeManager:
    """仅供现有测试覆盖旧依赖注入方式。"""

    def __init__(self, runtime: ConsoleRuntime) -> None:
        self.runtime = runtime

    @property
    def repository(self):
        return self.runtime.repository

    @property
    def broker(self):
        return self.runtime.broker

    async def initialize(self) -> None:
        await self.runtime.ensure_initialized()
