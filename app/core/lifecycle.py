"""FastAPI 生命周期：核心能力始终启动，Console/PDD 按配置可选启动。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from threading import Thread
from typing import AsyncIterator

from fastapi import FastAPI

from app.core.config import get_settings
from app.database.session import dispose_engine, get_session_factory
from app.factories.llm_factory import LLMFactory


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
    """启动核心应用；仅当 Console 开关打开时恢复 PDD 店铺运行时。"""
    settings = get_settings()
    manager = None

    if settings.console_enabled:
        # 延迟导入确保关闭 Console 时不加载 Playwright，也不创建多店运行时。
        from app.repositories.console_repository import ConsoleRepository
        from app.services.shop_runtime_manager import ShopRuntimeManager

        async def qa_provider():
            # 延迟导入避免生命周期模块与 API 依赖模块形成循环依赖。
            from app.api.dependencies import get_qa_service

            return await get_qa_service()

        manager = ShopRuntimeManager(
            ConsoleRepository(get_session_factory()),
            qa_provider,
            settings,
        )
        app.state.console_enabled = True
        app.state.shop_runtime_manager = manager
        await manager.initialize()
    else:
        app.state.console_enabled = False
        app.state.shop_runtime_manager = None

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
        if manager is not None:
            await manager.close()
        await dispose_engine()
