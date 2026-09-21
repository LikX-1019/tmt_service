"""多店铺浏览器运行时的创建、恢复与隔离管理。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agent.checkpoint import FileCheckpointStore
from app.agent.coordinator import StateCoordinator
from app.agent.dependencies import AgentCapabilities
from app.agent.graph import build_unified_chat_graph
from app.agent.routing import CustomerServiceRouter
from app.agent.runtime import AgentRuntime
from app.agent.service import CustomerServiceAgent
from app.core.config import Settings, get_settings
from app.core.logging import create_log_task
from app.core.exceptions import (
    InvalidRequestError,
    ResourceNotFoundError,
    ShopContextRequiredError,
    ShopLimitExceededError,
)
from app.integrations.pdd.base import ConnectorStatus, CustomerServiceConnector, ShopIdentity
from app.integrations.pdd.playwright_connector import PddPlaywrightConnector
from app.qa.service import QAService
from app.repositories.console_repository import ConsoleRepository
from app.repositories.product_repository import ProductRepository
from app.rules.registry import default_rule_registry
from app.services.console_runtime import ConsoleRuntime
from app.services.contextual_fallback_service import ContextualFallbackService
from app.services.event_broker import EventBroker
from app.services.product_resolver import ProductResolver
from app.services.product_service import (
    HttpProductClient,
    ProductAnswerService,
)
from app.services.semantic_product_resolver import SemanticProductResolver
from app.services.social_router import SocialRouter


logger = logging.getLogger(__name__)
QAProvider = Callable[[], Awaitable[QAService]]
ConnectorFactory = Callable[[dict[str, Any], int], CustomerServiceConnector]


class ShopRuntimeManager:
    """每个店铺持有一个 ConsoleRuntime，只有仓储、QA 与事件广播器共享。"""

    def __init__(
        self,
        repository: ConsoleRepository,
        qa_provider: QAProvider,
        settings: Settings | None = None,
        *,
        connector_factory: ConnectorFactory | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or get_settings()
        self.broker = EventBroker()
        self._qa_provider = qa_provider
        self._connector_factory = connector_factory or self._default_connector
        self._runtimes: dict[str, ConsoleRuntime] = {}
        self._restart_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._initialized = False
        self._agent_runtime = self._build_agent_runtime()

    async def _load_pdd_greeting_config(self, shop_id: str | None) -> dict[str, Any]:
        """Load greeting configuration for the PDD Graph without channel orchestration."""
        if not shop_id:
            return {
                "enabled": True,
                "trigger_groups": {},
                "reply_templates": {},
            }
        return await self.repository.get_greeting_config(shop_id)

    def _build_agent_runtime(self) -> AgentRuntime:
        """Compose the shared business Graph used by every PDD shop runtime."""
        product_provider = ProductRepository(HttpProductClient(self.settings))
        capabilities = AgentCapabilities(
            rules=default_rule_registry(),
            social_router=SocialRouter(),
            product_resolver=ProductResolver(self.settings),
            semantic_products=SemanticProductResolver(),
            product_answers=ProductAnswerService(),
            fallbacks=ContextualFallbackService(),
            qa_provider=self._qa_provider,
            products=product_provider,
            greeting_agent=CustomerServiceAgent(),
            greeting_config_loader=self._load_pdd_greeting_config,
            pdd_router=CustomerServiceRouter(),
        )
        coordinator = StateCoordinator(FileCheckpointStore())
        return AgentRuntime(
            build_unified_chat_graph(capabilities, coordinator=coordinator),
            coordinator=coordinator,
        )

    def _profile_path(self, shop: dict[str, Any]) -> Path:
        if shop.get("browser_profile_key") == "legacy":
            return Path(self.settings.pdd_chrome_profile_dir)
        return Path(self.settings.pdd_chrome_profile_root) / str(
            shop["browser_profile_key"]
        )

    def _default_connector(
        self, shop: dict[str, Any], slot: int
    ) -> CustomerServiceConnector:
        return PddPlaywrightConnector(
            self.settings,
            profile_dir=self._profile_path(shop),
            window_slot=slot,
            require_identity=True,
        )

    def _build_runtime(self, shop: dict[str, Any], slot: int) -> ConsoleRuntime:
        shop_id = str(shop["id"])

        async def identity_handler(identity: ShopIdentity) -> None:
            await self._identify(shop_id, identity)

        async def status_handler(snapshot) -> None:
            if snapshot.status == ConnectorStatus.ERROR:
                self._schedule_restart(shop_id)

        return ConsoleRuntime(
            self.repository,
            self._connector_factory(shop, slot),
            self.settings,
            shop_id=shop_id,
            broker=self.broker,
            identity_handler=identity_handler,
            runtime_status_handler=status_handler,
            agent_runtime=self._agent_runtime,
        )

    def _schedule_restart(self, shop_id: str) -> None:
        current = self._restart_tasks.get(shop_id)
        if current is not None and not current.done():
            return
        self._restart_tasks[shop_id] = create_log_task(
            self._restart_after_failure(shop_id),
            name=f"restart-shop-{shop_id}",
            shop_id=shop_id,
        )

    async def _restart_after_failure(self, shop_id: str) -> None:
        try:
            for attempt in range(3):
                shop = await self.repository.get_shop(shop_id)
                runtime = self._runtimes.get(shop_id)
                if (
                    shop is None
                    or runtime is None
                    or not shop["desired_online"]
                    or shop["lifecycle_status"] == "disabled"
                ):
                    return
                await asyncio.sleep(2**attempt)
                try:
                    await runtime.stop_connector()
                    snapshot = await runtime.start_connector()
                except Exception:
                    logger.exception(
                        "shop_runtime_restart_failed",
                        extra={
                            "event": "shop_runtime_restart_failed",
                            "shop_id": shop_id,
                            "attempt": attempt + 1,
                        },
                    )
                    continue
                if snapshot["status"] != ConnectorStatus.ERROR.value:
                    await self.repository.update_shop_state(
                        shop_id, last_error_code=None
                    )
                    return
            await self.repository.update_shop_state(
                shop_id, last_error_code="BROWSER_RESTART_EXHAUSTED"
            )
        finally:
            self._restart_tasks.pop(shop_id, None)

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            shops = await self.repository.list_shops()
            if not shops:
                await self.repository.ensure_shop(self.settings.default_shop_name)
                shops = await self.repository.list_shops()
            for slot, shop in enumerate(shops):
                if shop["lifecycle_status"] != "disabled":
                    self._runtimes[str(shop["id"])] = self._build_runtime(shop, slot)
            self._initialized = True

        semaphore = asyncio.Semaphore(2)

        async def restore(shop: dict[str, Any]) -> None:
            if not shop["desired_online"] or shop["lifecycle_status"] not in {
                "active",
                "provisioning",
            }:
                return
            async with semaphore:
                try:
                    await self.start(str(shop["id"]), restoring=True)
                except Exception:
                    logger.exception(
                        "shop_runtime_restore_failed",
                        extra={"event": "shop_runtime_restore_failed", "shop_id": shop["id"]},
                    )

        await asyncio.gather(*(restore(shop) for shop in shops))

    async def close(self) -> None:
        for task in self._restart_tasks.values():
            task.cancel()
        await asyncio.gather(*self._restart_tasks.values(), return_exceptions=True)
        self._restart_tasks.clear()
        await asyncio.gather(
            *(runtime.close() for runtime in list(self._runtimes.values())),
            return_exceptions=True,
        )
        self._runtimes.clear()

    async def list_shops(self) -> list[dict[str, Any]]:
        await self.initialize()
        shops = await self.repository.list_shops()
        for shop in shops:
            runtime = self._runtimes.get(str(shop["id"]))
            if runtime is None:
                shop["connector"] = {"status": "stopped", "detail": None, "changed_at": None}
            else:
                shop["connector"] = await runtime.connector_status()
        return shops

    async def get_shop(self, shop_id: str) -> dict[str, Any]:
        await self.initialize()
        shop = await self.repository.get_shop(shop_id)
        if shop is None:
            raise ResourceNotFoundError("店铺不存在")
        runtime = self._runtimes.get(shop_id)
        shop["connector"] = (
            await runtime.connector_status()
            if runtime
            else {"status": "stopped", "detail": None, "changed_at": None}
        )
        return shop

    async def get_runtime(self, shop_id: str) -> ConsoleRuntime:
        await self.initialize()
        shop = await self.repository.get_shop(shop_id)
        if shop is None or shop["lifecycle_status"] == "disabled":
            raise ResourceNotFoundError("店铺不存在或已停用")
        runtime = self._runtimes.get(shop_id)
        if runtime is None:
            runtime = self._build_runtime(shop, len(self._runtimes))
            self._runtimes[shop_id] = runtime
        await runtime.ensure_initialized()
        return runtime

    async def sole_active_runtime(self) -> ConsoleRuntime:
        shops = [
            shop
            for shop in await self.repository.list_shops(include_disabled=False)
            if shop["lifecycle_status"] == "active" and shop["enabled"]
        ]
        if len(shops) != 1:
            raise ShopContextRequiredError()
        return await self.get_runtime(str(shops[0]["id"]))

    async def provision(self) -> dict[str, Any]:
        await self.initialize()
        profile_key = f"shop-{uuid4().hex}"
        shop = await self.repository.create_provisioning_shop(
            browser_profile_key=profile_key
        )
        shop_id = str(shop["id"])
        self._runtimes[shop_id] = self._build_runtime(shop, len(self._runtimes))
        await self.start(shop_id)
        return await self.get_shop(shop_id)

    async def _online_count(self, *, excluding: str | None = None) -> int:
        count = 0
        for shop_id, runtime in self._runtimes.items():
            if shop_id == excluding:
                continue
            status = await runtime.connector_status()
            if status["status"] != ConnectorStatus.STOPPED.value:
                count += 1
        return count

    async def start(
        self, shop_id: str, *, restoring: bool = False
    ) -> dict[str, Any]:
        runtime = await self.get_runtime(shop_id)
        shop = await self.repository.get_shop(shop_id)
        assert shop is not None
        if shop["lifecycle_status"] in {"disabled", "failed"}:
            raise InvalidRequestError("当前店铺状态不允许上线")
        current = await runtime.connector_status()
        if current["status"] not in {"stopped", "error"}:
            return current
        if await self._online_count(excluding=shop_id) >= self.settings.pdd_max_active_shops:
            raise ShopLimitExceededError()
        await self.repository.update_shop_state(shop_id, desired_online=True)
        try:
            return await runtime.start_connector()
        except Exception:
            if not restoring:
                await self.repository.update_shop_state(
                    shop_id, last_error_code="CONNECTOR_START_FAILED"
                )
            raise

    async def stop(self, shop_id: str) -> dict[str, Any]:
        await self.initialize()
        shop = await self.repository.get_shop(shop_id)
        if shop is None:
            raise ResourceNotFoundError("店铺不存在")
        await self.repository.update_shop_state(shop_id, desired_online=False)
        await self.repository.cancel_queued_jobs(shop_id)
        restart = self._restart_tasks.pop(shop_id, None)
        if restart is not None:
            restart.cancel()
            await asyncio.gather(restart, return_exceptions=True)
        runtime = self._runtimes.get(shop_id)
        if runtime is None:
            return {"status": "stopped", "detail": "连接器已停止", "changed_at": None}
        await runtime.prepare_offline()
        return await runtime.stop_connector()

    async def disable(self, shop_id: str) -> dict[str, Any]:
        await self.initialize()
        shop = await self.repository.get_shop(shop_id)
        if shop is None:
            raise ResourceNotFoundError("店铺不存在")
        await self.repository.update_shop_state(shop_id, desired_online=False)
        await self.repository.cancel_queued_jobs(shop_id)
        restart = self._restart_tasks.pop(shop_id, None)
        if restart is not None:
            restart.cancel()
            await asyncio.gather(restart, return_exceptions=True)
        runtime = self._runtimes.get(shop_id)
        if runtime is not None:
            await runtime.prepare_offline()
            await runtime.stop_connector()
        result = await self.repository.update_shop_state(
            shop_id,
            lifecycle_status="disabled",
            desired_online=False,
            enabled=False,
        )
        if runtime is not None:
            await runtime.close()
        self._runtimes.pop(shop_id, None)
        return result

    async def focus(self, shop_id: str) -> dict[str, Any]:
        return await (await self.get_runtime(shop_id)).focus_connector()

    async def status(self, shop_id: str) -> dict[str, Any]:
        return await (await self.get_runtime(shop_id)).connector_status()

    async def _identify(self, shop_id: str, identity: ShopIdentity) -> None:
        shop, duplicate_id = await self.repository.identify_shop(
            shop_id,
            platform_shop_id=identity.platform_shop_id,
            name=identity.name,
        )
        runtime = self._runtimes.get(shop_id)
        if duplicate_id is not None:
            if runtime is not None:
                create_log_task(
                    runtime.stop_connector(),
                    name=f"stop-duplicate-shop-{shop_id}",
                    shop_id=shop_id,
                )
            event = await self.repository.add_event(
                "shop.duplicate",
                {"existing_shop_id": duplicate_id},
                shop_id=shop_id,
            )
            await self.broker.publish(event)
            return
        if runtime is not None:
            runtime._shop = shop
        event = await self.repository.add_event(
            "shop.identified", {"name": shop["name"]}, shop_id=shop_id
        )
        await self.broker.publish(event)
