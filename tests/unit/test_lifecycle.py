"""FastAPI lifecycle boundary tests for the optional Console/PDD channel."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

import app.core.lifecycle as lifecycle
from app.api.dependencies import get_qa_service
from app.qa.models import QAResult
from main import app as fastapi_app


class FakeShopRuntimeManager:
    instances: list["FakeShopRuntimeManager"] = []

    def __init__(self, repository: Any, qa_provider: Any, settings: Any) -> None:
        self.repository = repository
        self.qa_provider = qa_provider
        self.settings = settings
        self.initialized = False
        self.closed = False
        self.instances.append(self)

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.closed = True


class StubQAService:
    async def answer(self, query: str) -> QAResult:
        return QAResult(answer=f"已回答：{query}", route="faq", confidence=1.0)


@pytest.mark.asyncio
async def test_console_enabled_lifecycle_initializes_and_closes_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeShopRuntimeManager.instances.clear()
    settings = SimpleNamespace(console_enabled=True, llm_warmup_on_startup=False)
    monkeypatch.setattr(lifecycle, "get_settings", lambda: settings)
    monkeypatch.setattr(lifecycle, "get_session_factory", lambda: object())
    dispose_engine = AsyncMock()
    monkeypatch.setattr(lifecycle, "dispose_engine", dispose_engine)
    monkeypatch.setattr(
        "app.services.shop_runtime_manager.ShopRuntimeManager",
        FakeShopRuntimeManager,
    )
    application = FastAPI()

    async with lifecycle.lifespan(application):
        assert len(FakeShopRuntimeManager.instances) == 1
        manager = FakeShopRuntimeManager.instances[0]
        assert application.state.shop_runtime_manager is manager
        assert manager.initialized is True
        assert manager.closed is False

    assert manager.closed is True
    dispose_engine.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_console_disabled_lifecycle_does_not_create_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(console_enabled=False, llm_warmup_on_startup=False)

    def fail_construction(*args: object, **kwargs: object) -> None:
        raise AssertionError("Console disabled must not construct ShopRuntimeManager")

    monkeypatch.setattr(lifecycle, "get_settings", lambda: settings)
    monkeypatch.setattr(lifecycle, "get_session_factory", lambda: object())
    dispose_engine = AsyncMock()
    monkeypatch.setattr(lifecycle, "dispose_engine", dispose_engine)
    monkeypatch.setattr(
        "app.services.shop_runtime_manager.ShopRuntimeManager",
        fail_construction,
    )
    application = FastAPI()

    async with lifecycle.lifespan(application):
        assert application.state.console_enabled is False
        assert application.state.shop_runtime_manager is None

    dispose_engine.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_console_disabled_keeps_health_qa_and_qa_demo_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(console_enabled=False, llm_warmup_on_startup=False)
    monkeypatch.setattr(lifecycle, "get_settings", lambda: settings)
    monkeypatch.setattr(lifecycle, "get_session_factory", lambda: object())
    monkeypatch.setattr(lifecycle, "dispose_engine", AsyncMock())
    fastapi_app.dependency_overrides[get_qa_service] = lambda: StubQAService()
    try:
        async with lifecycle.lifespan(fastapi_app):
            transport = httpx.ASGITransport(app=fastapi_app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                health = await client.get("/health")
                qa = await client.post("/api/v1/qa", json={"query": "退货规则"})
                qa_demo = await client.get("/qa-demo")
                console = await client.get("/api/v1/shops")

        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert qa.status_code == 200
        assert qa.json()["data"]["answer"] == "已回答：退货规则"
        assert qa_demo.status_code == 200
        assert console.status_code == 503
        assert console.json()["code"] == "CONSOLE_UNAVAILABLE"
        assert console.json()["message"] == "客服控制台未启用"
    finally:
        fastapi_app.dependency_overrides.clear()
        if hasattr(fastapi_app.state, "console_enabled"):
            del fastapi_app.state.console_enabled
        if hasattr(fastapi_app.state, "shop_runtime_manager"):
            del fastapi_app.state.shop_runtime_manager
