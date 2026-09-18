"""应用生命周期管理，在后台预热首个大模型实例。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from threading import Thread
from typing import AsyncIterator

from fastapi import FastAPI

from app.core.config import get_settings
from app.database.session import dispose_engine, get_session_factory
from app.factories.llm_factory import LLMFactory
from app.repositories.console_repository import ConsoleRepository
from app.services.shop_runtime_manager import ShopRuntimeManager


logger = logging.getLogger(__name__)


def _warm_up_llm() -> None:
    """在后台线程加载模型依赖并填充 Factory 缓存，不发起模型请求。"""
    logger.info("llm_warmup_started", extra={"event": "llm_warmup_started"})
    try:
        LLMFactory.create()
    except Exception:
        # 预热失败不能阻止 API 启动；完整异常会写入错误日志，实际请求仍可重试。
        logger.exception("llm_warmup_failed", extra={"event": "llm_warmup_failed"})
        return
    logger.info("llm_warmup_succeeded", extra={"event": "llm_warmup_succeeded"})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动后台模型预热任务，同时保持页面和健康检查立即可用。"""
    settings = get_settings()

    async def qa_provider():
        # 延迟导入避免生命周期模块与 API 依赖模块形成循环依赖。
        from app.api.dependencies import get_qa_service

        return await get_qa_service()

    manager = ShopRuntimeManager(
        ConsoleRepository(get_session_factory()),
        qa_provider,
        settings,
    )
    app.state.shop_runtime_manager = manager
    await manager.initialize()
    if settings.llm_warmup_on_startup:
        warmup_thread = Thread(
            target=_warm_up_llm,
            name="llm-warmup",
            daemon=True,
        )
        warmup_thread.start()
        app.state.llm_warmup_thread = warmup_thread
    try:
        yield
    finally:
        await manager.close()
        await dispose_engine()
