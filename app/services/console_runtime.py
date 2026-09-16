"""连接浏览器、数据库、SSE 与自动回复决策的控制台运行时。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import date, datetime
from hashlib import sha256
from typing import Any, Awaitable, Callable

from app.agent.service import CustomerContext, CustomerServiceAgent
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ConsoleUnavailableError,
    InvalidRequestError,
    ResourceNotFoundError,
)
from app.integrations.pdd.base import (
    BrowserMessage,
    ConnectorError,
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerProfile,
    CustomerServiceConnector,
    SendUncertainError,
)
from app.qa.service import QAService
from app.repositories.console_repository import ConsoleRepository
from app.services.auto_reply_policy import AutoReplyPolicy, Calibration
from app.services.event_broker import EventBroker
from app.services.message_asset_storage import MessageAssetStorage


logger = logging.getLogger(__name__)
QAProvider = Callable[[], Awaitable[QAService]]


class ConsoleRuntime:
    """单店运行时；浏览器任务和发送任务全部在同一事件循环串行协调。"""

    def __init__(
        self,
        repository: ConsoleRepository,
        connector: CustomerServiceConnector,
        qa_provider: QAProvider,
        settings: Settings | None = None,
        agent: CustomerServiceAgent | None = None,
    ) -> None:
        self._repository = repository
        self._connector = connector
        self._qa_provider = qa_provider
        self._settings = settings or get_settings()
        self._agent = agent or CustomerServiceAgent()
        self._broker = EventBroker()
        self._shop: dict[str, Any] | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._send_queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._debounces: dict[str, asyncio.Task[None]] = {}
        self._batches: dict[str, list[BrowserMessage]] = {}
        calibration = Calibration.load(
            self._settings.auto_reply_calibration_path,
            min_precision=self._settings.auto_reply_min_precision,
            min_samples=self._settings.auto_reply_min_samples,
        )
        self._policy = AutoReplyPolicy(calibration)
        self._asset_storage = MessageAssetStorage(
            self._settings.message_asset_dir,
            max_bytes=self._settings.message_asset_max_bytes,
        )

    @property
    def broker(self) -> EventBroker:
        return self._broker

    @property
    def repository(self) -> ConsoleRepository:
        return self._repository

    async def ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                self._shop = await self._repository.ensure_shop(
                    self._settings.default_shop_name
                )
                expired_assets = await self._repository.cleanup(
                    self._settings.message_retention_days,
                    self._settings.audit_retention_days,
                )
                for storage_key in expired_assets:
                    try:
                        self._asset_storage.delete(storage_key)
                    except Exception:
                        logger.exception("message_asset_cleanup_failed", extra={"event": "message_asset_cleanup_failed"})
                self._worker = asyncio.create_task(
                    self._send_worker(), name="console-send-worker"
                )
                for job in await self._repository.recover_queued_jobs():
                    self._send_queue.put_nowait(str(job["id"]))
                self._initialized = True
            except Exception as exc:
                logger.exception("console_initialize_failed", extra={"event": "console_initialize_failed"})
                raise ConsoleUnavailableError("控制台数据库不可用") from exc

    async def close(self) -> None:
        for task in self._debounces.values():
            task.cancel()
        await asyncio.gather(*self._debounces.values(), return_exceptions=True)
        self._debounces.clear()
        await self._connector.stop()
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event_payload = self._sanitize_event_payload(event_type, payload)
        event = await self._repository.add_event(event_type, event_payload)
        await self._broker.publish(event)
        return event

    @staticmethod
    def _sanitize_event_payload(
        event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """SSE 事件只持久化刷新所需字段，避免把聊天正文保存 90 天。"""
        fields = {
            "conversation.upserted": {"id", "state", "unread_count"},
            "message.created": {
                "id",
                "conversation_id",
                "direction",
                "kind",
                "occurred_at",
            },
            "outbound.updated": {
                "id",
                "conversation_id",
                "source",
                "status",
                "error_code",
            },
            "reply.decision": {
                "id",
                "conversation_id",
                "route",
                "action",
                "qa_code",
                "risk_reason",
            },
        }.get(event_type)
        if fields is None:
            return payload
        return {key: value for key, value in payload.items() if key in fields}

    async def start_connector(self) -> dict[str, Any]:
        await self.ensure_initialized()
        try:
            current = await self._connector.status()
            if current.status in {ConnectorStatus.DEGRADED, ConnectorStatus.ERROR}:
                await self._connector.stop()
            snapshot = await self._connector.start(
                self._on_message, self._on_connector_status, self._on_profile
            )
        except ConnectorError as exc:
            raise ConsoleUnavailableError(str(exc)) from exc
        return self._status_dict(snapshot)

    async def stop_connector(self) -> dict[str, Any]:
        await self.ensure_initialized()
        return self._status_dict(await self._connector.stop())

    async def connector_status(self) -> dict[str, Any]:
        return self._status_dict(await self._connector.status())

    async def connector_diagnostics(self) -> dict[str, object]:
        return await self._connector.diagnostics()

    async def message_asset(self, asset_id: str) -> tuple[str, str]:
        await self.ensure_initialized()
        item = await self._repository.get_message_asset(asset_id)
        if item is None:
            raise ResourceNotFoundError("媒体资源不存在")
        try:
            path = self._asset_storage.resolve(str(item["storage_key"]))
        except ValueError as exc:
            raise ResourceNotFoundError("媒体资源不存在") from exc
        if not path.is_file():
            raise ResourceNotFoundError("媒体文件不存在")
        return str(path), str(item["mime_type"])

    @staticmethod
    def _status_dict(snapshot: ConnectorStatusSnapshot) -> dict[str, Any]:
        return {
            "status": snapshot.status.value,
            "detail": snapshot.detail,
            "changed_at": snapshot.changed_at.isoformat() if snapshot.changed_at else None,
        }

    async def _on_connector_status(self, snapshot: ConnectorStatusSnapshot) -> None:
        if self._initialized:
            await self._emit("connector.status", self._status_dict(snapshot))

    async def _on_profile(self, profile: CustomerProfile) -> None:
        await self.ensure_initialized()
        assert self._shop is not None
        conversation, changed = await self._repository.upsert_customer_profile(
            self._shop["id"], asdict(profile)
        )
        if changed:
            await self._emit("conversation.upserted", conversation)

    async def _on_message(self, message: BrowserMessage) -> None:
        await self.ensure_initialized()
        assert self._shop is not None
        payload = asdict(message)
        conversation, stored, inserted = await self._repository.ingest_message(
            self._shop["id"], payload
        )
        if not inserted:
            return
        await self._emit("conversation.upserted", conversation)
        await self._emit("message.created", {**stored, "conversation_id": conversation["id"]})
        if message.direction != "inbound" or message.is_backfill:
            return
        if message.kind != "text" or not message.content:
            batch_key = sha256(message.fingerprint.encode()).hexdigest()
            decision = await self._repository.add_decision(
                conversation["id"],
                {
                    "batch_key": batch_key,
                    "route": "unsupported",
                    "action": "suggest",
                    "risk_reason": "非文本消息需人工处理",
                    "policy_version": self._settings.auto_reply_policy_version,
                },
            )
            await self._emit("reply.decision", {**decision, "conversation_id": conversation["id"]})
            return
        conversation_id = str(conversation["id"])
        self._batches.setdefault(conversation_id, []).append(message)
        previous = self._debounces.pop(conversation_id, None)
        if previous:
            previous.cancel()
        self._debounces[conversation_id] = asyncio.create_task(
            self._evaluate_after_delay(conversation_id),
            name=f"auto-reply-{conversation_id}",
        )

    async def _evaluate_after_delay(self, conversation_id: str) -> None:
        try:
            await asyncio.sleep(self._settings.auto_reply_debounce_seconds)
            batch = self._batches.pop(conversation_id, [])
            if batch:
                await self._evaluate_batch(conversation_id, batch)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("auto_reply_evaluation_failed", extra={"event": "auto_reply_evaluation_failed"})
        finally:
            if self._debounces.get(conversation_id) is asyncio.current_task():
                self._debounces.pop(conversation_id, None)

    async def _evaluate_batch(
        self, conversation_id: str, batch: list[BrowserMessage]
    ) -> None:
        conversation = await self._repository.get_conversation(conversation_id)
        if conversation is None:
            return
        assert self._shop is not None
        shop = await self._repository.get_shop(self._shop["id"])
        status = await self._connector.status()
        allow_auto = bool(
            shop
            and shop["global_auto_reply_enabled"]
            and conversation["auto_reply_enabled"]
            and status.status == ConnectorStatus.READY
        )
        query = "\n".join(message.content or "" for message in batch[-5:]).strip()
        batch_key = sha256("|".join(message.fingerprint for message in batch).encode()).hexdigest()
        decision_payload: dict[str, Any] | None = None
        try:
            agent_reply = await self._agent.run(
                query,
                CustomerContext(
                    customer_id=str(conversation.get("customer_id") or "") or None,
                    platform_customer_id=(
                        str(conversation.get("platform_conversation_id") or "") or None
                    ),
                    display_name=str(conversation.get("display_name") or "") or None,
                    shop_id=str(shop.get("id") or "") if shop else None,
                    shop_name=str(shop.get("name") or "") if shop else None,
                    goods_id=str(conversation.get("goods_id") or "") or None,
                    goods_name=str(conversation.get("goods_name") or "") or None,
                ),
            )
        except Exception:
            # 意图模型不可用时保留既有 QA/RAG 降级路径，不让新增分支阻断接待。
            logger.exception(
                "agent_intent_routing_failed",
                extra={"event": "agent_intent_routing_failed"},
            )
        else:
            if agent_reply.intent == "daily_greeting" and agent_reply.answer:
                decision_payload = {
                    "batch_key": batch_key,
                    "route": "greeting",
                    "action": "auto_send" if allow_auto else "suggest",
                    "top_score": agent_reply.confidence,
                    "risk_reason": "日常打招呼由小满客服生成",
                    "suggested_answer": agent_reply.answer,
                    "policy_version": self._settings.auto_reply_policy_version,
                }

        if decision_payload is None:
            try:
                qa_service = await self._qa_provider()
                result = await qa_service.answer(
                    query,
                    product_code=str(conversation.get("goods_id") or "") or None,
                )
                policy = self._policy.evaluate(
                    query, result, conversation, allow_auto=allow_auto
                )
                decision_payload = {
                    "batch_key": batch_key,
                    "route": policy.route,
                    "action": policy.action,
                    "qa_code": policy.qa_code,
                    "top_score": policy.top_score,
                    "score_margin": policy.margin,
                    "risk_reason": policy.reason,
                    "suggested_answer": policy.answer,
                    "policy_version": self._settings.auto_reply_policy_version,
                }
            except Exception:
                logger.exception("qa_suggestion_failed", extra={"event": "qa_suggestion_failed"})
                decision_payload = {
                    "batch_key": batch_key,
                    "route": "error",
                    "action": "suggest",
                    "risk_reason": "知识库或模型不可用",
                    "policy_version": self._settings.auto_reply_policy_version,
                }
        decision = await self._repository.add_decision(
            conversation_id, decision_payload
        )
        await self._emit("reply.decision", {**decision, "conversation_id": conversation_id})
        if decision.get("action") == "auto_send" and decision.get("suggested_answer"):
            job, created = await self._repository.create_outbound_job(
                conversation_id,
                client_request_id=f"auto:{batch_key}",
                source="auto",
                content=str(decision["suggested_answer"])[:400],
            )
            if created:
                await self._emit("outbound.updated", job)
                self._send_queue.put_nowait(str(job["id"]))

    async def _send_worker(self) -> None:
        while True:
            job_id = await self._send_queue.get()
            try:
                await self._send_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("outbound_worker_failed", extra={"event": "outbound_worker_failed"})
                try:
                    job = await self._repository.get_job(job_id)
                    if job and job["status"] == "sending":
                        job = await self._repository.update_job(
                            job_id,
                            status="uncertain",
                            error_code="WORKER_UNCERTAIN",
                        )
                        await self._pause_after_send_failure(job)
                        await self._emit("outbound.updated", job)
                except Exception:
                    logger.exception(
                        "outbound_worker_recovery_failed",
                        extra={"event": "outbound_worker_recovery_failed"},
                    )
            finally:
                self._send_queue.task_done()

    async def _send_job(self, job_id: str) -> None:
        job = await self._repository.get_job(job_id)
        if not job or job["status"] != "queued":
            return
        conversation = await self._repository.get_conversation(str(job["conversation_id"]))
        if conversation is None:
            return
        job = await self._repository.update_job(job_id, status="sending")
        await self._emit("outbound.updated", job)
        try:
            receipt = await self._connector.send_message(
                str(conversation["platform_conversation_id"]), str(job["content"])
            )
        except SendUncertainError as exc:
            job = await self._repository.update_job(
                job_id,
                status="uncertain",
                error_code="SEND_UNCERTAIN",
                clicked_at=exc.clicked_at,
            )
            await self._pause_after_send_failure(job)
        except ConnectorError:
            job = await self._repository.update_job(
                job_id, status="failed", error_code="CONNECTOR_ERROR"
            )
            await self._pause_after_send_failure(job)
        else:
            job = await self._repository.update_job(
                job_id,
                status="sent",
                clicked_at=receipt.clicked_at,
                sent_at=receipt.confirmed_at,
                responder_name=receipt.responder_name,
            )
        await self._emit("outbound.updated", job)

    async def _pause_after_send_failure(self, job: dict[str, Any]) -> None:
        if self._shop:
            await self._repository.set_global_automation(self._shop["id"], False)
            await self._emit(
                "automation.updated",
                {"enabled": False, "reason": "发送失败，自动回复已安全暂停"},
            )

    async def list_conversations(
        self, *, limit: int, state: str | None, search: str | None, before: datetime | None
    ) -> list[dict[str, Any]]:
        await self.ensure_initialized()
        assert self._shop is not None
        return await self._repository.list_conversations(
            self._shop["id"], limit=limit, state=state, search=search, before=before
        )

    async def list_customers(
        self, *, limit: int, search: str | None
    ) -> list[dict[str, Any]]:
        await self.ensure_initialized()
        assert self._shop is not None
        return await self._repository.list_customers(
            self._shop["id"], limit=limit, search=search
        )

    async def customer_messages(
        self, customer_id: str, *, message_date: date | None, limit: int
    ) -> list[dict[str, Any]]:
        await self.ensure_initialized()
        return await self._repository.list_customer_messages(
            customer_id, message_date=message_date, limit=limit
        )

    async def conversation_detail(
        self, conversation_id: str, *, limit: int, before: datetime | None
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        conversation = await self._repository.get_conversation(conversation_id)
        if conversation is None:
            raise ResourceNotFoundError("会话不存在")
        return {
            "conversation": conversation,
            "messages": await self._repository.list_messages(
                conversation_id, limit=limit, before=before
            ),
            "decision": await self._repository.latest_decision(conversation_id),
        }

    async def mark_conversation_read(self, conversation_id: str) -> dict[str, Any]:
        await self.ensure_initialized()
        try:
            return await self._repository.mark_conversation_read(conversation_id)
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc

    async def reply(
        self, conversation_id: str, *, content: str, client_request_id: str
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        status = await self._connector.status()
        if status.status != ConnectorStatus.READY:
            raise ConsoleUnavailableError("拼多多连接器未就绪")
        if await self._repository.get_conversation(conversation_id) is None:
            raise ResourceNotFoundError("会话不存在")
        try:
            job, created = await self._repository.create_outbound_job(
                conversation_id,
                client_request_id=client_request_id,
                source="manual",
                content=content,
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        if created:
            await self._emit("outbound.updated", job)
            self._send_queue.put_nowait(str(job["id"]))
        return job

    async def set_conversation_automation(
        self, conversation_id: str, enabled: bool
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        if enabled:
            assert self._shop is not None
            shop = await self._repository.get_shop(self._shop["id"])
            if not shop or not shop["global_auto_reply_enabled"]:
                raise InvalidRequestError("请先开启全局自动接待")
            status = await self._connector.status()
            if status.status != ConnectorStatus.READY:
                raise ConsoleUnavailableError("拼多多连接器就绪后才能恢复自动接待")
        try:
            conversation = await self._repository.set_conversation_automation(
                conversation_id, enabled
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        await self._emit("conversation.upserted", conversation)
        return conversation

    async def automation(self) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop is not None
        shop = await self._repository.get_shop(self._shop["id"])
        return {"enabled": bool(shop and shop["global_auto_reply_enabled"])}

    async def set_automation(self, enabled: bool) -> dict[str, Any]:
        await self.ensure_initialized()
        if enabled:
            status = await self._connector.status()
            if status.status != ConnectorStatus.READY:
                raise ConsoleUnavailableError("拼多多连接器就绪后才能开启自动接待")
        assert self._shop is not None
        result = await self._repository.set_global_automation(self._shop["id"], enabled)
        await self._emit("automation.updated", result)
        return result
