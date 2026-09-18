"""FastAPI 依赖提供模块，集中管理接口所需服务实例。"""

from __future__ import annotations

import asyncio
from functools import lru_cache

from fastapi import Request

from app.core.config import get_settings
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
from app.services.chat_service import ChatService
from app.services.console_runtime import ConsoleRuntime
from app.services.shop_runtime_manager import ShopRuntimeManager

_qa_service: QAService | None = None
_qa_service_lock = asyncio.Lock()


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    """返回进程级无状态聊天服务，便于缓存复用和测试替换。"""
    return ChatService()


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
