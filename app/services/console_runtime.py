"""连接浏览器、数据库、SSE 与自动回复决策的控制台运行时。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Awaitable, Callable

from app.agent.greeting import default_reply_templates, default_trigger_groups
from app.agent.channels.pdd_adapter import (
    PDDChannelDecision,
    evaluate_pdd_channel_policy,
    invoke_pdd_agent,
)
from app.agent.runtime import AgentRuntime
from app.agent.routing import CustomerServiceRouter
from app.agent.service import CustomerContext, CustomerServiceAgent
from app.core.config import Settings, get_settings
from app.core.logging import create_log_task
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
    ShopIdentity,
)
from app.qa.service import QAService
from app.repositories.console_repository import ConsoleRepository
from app.services.auto_reply_policy import AutoReplyPolicy, Calibration
from app.services.event_broker import EventBroker
from app.services.message_asset_storage import MessageAssetStorage
from app.services.social_router import SocialRouter
from app.services.product_service import (
    HttpProductClient,
    ProductAnswerService,
    ProductLookupError,
    ProductProvider,
)


logger = logging.getLogger(__name__)
QAProvider = Callable[[], Awaitable[QAService]]
IdentityHandler = Callable[[ShopIdentity], Awaitable[None]]
RuntimeStatusHandler = Callable[[ConnectorStatusSnapshot], Awaitable[None]]
KNOWLEDGE_GAP_REASON_CODES = frozenset(
    {
        "ambiguous_product",
        "fallback",
        "knowledge_inactive",
        "knowledge_not_reviewed",
        "knowledge_retrieval_disabled",
        "low_rag_margin",
        "low_rag_score",
        "missing_standard_answer",
        "product_context_missing",
    }
)


class ConsoleRuntime:
    """单店运行时；浏览器任务和发送任务全部在同一事件循环串行协调。"""

    def __init__(
        self,
        repository: ConsoleRepository,
        connector: CustomerServiceConnector,
        qa_provider: QAProvider,
        settings: Settings | None = None,
        agent: CustomerServiceAgent | None = None,
        router: CustomerServiceRouter | None = None,
        product_provider: ProductProvider | None = None,
        product_answer_service: ProductAnswerService | None = None,
        social_router: SocialRouter | None = None,
        *,
        shop_id: str | None = None,
        broker: EventBroker | None = None,
        identity_handler: IdentityHandler | None = None,
        runtime_status_handler: RuntimeStatusHandler | None = None,
        pdd_agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self._repository = repository
        self._connector = connector
        self._qa_provider = qa_provider
        self._settings = settings or get_settings()
        self._agent = agent or CustomerServiceAgent()
        self._router = router or CustomerServiceRouter()
        self._product_provider = product_provider or HttpProductClient(self._settings)
        self._product_answer_service = product_answer_service or ProductAnswerService()
        self._social_router = social_router or SocialRouter()
        self._broker = broker or EventBroker()
        self._legacy_single_shop_mode = shop_id is None
        self._shop_id = shop_id
        self._identity_handler = identity_handler
        self._runtime_status_handler = runtime_status_handler
        self._pdd_agent_runtime = pdd_agent_runtime
        # Directly constructed test/embedding runtimes predate the composition-root
        # injection. Production always injects the graph runtime from ShopRuntimeManager.
        self._pdd_runtime_mode = (
            self._settings.pdd_agent_runtime
            if pdd_agent_runtime is not None
            else "legacy"
        )
        self._shop: dict[str, Any] | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._send_queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._jobs_recovered = False
        self._accepting_work = True
        self._debounces: dict[str, asyncio.Task[None]] = {}
        self._batches: dict[str, list[BrowserMessage]] = {}
        calibration = Calibration.load(
            self._settings.auto_reply_calibration_path,
            min_precision=self._settings.auto_reply_min_precision,
            min_samples=self._settings.auto_reply_min_samples,
        )
        self._policy = AutoReplyPolicy(
            calibration,
            rag_mode=self._settings.auto_reply_rag_mode,
            rag_score_threshold=self._settings.rag_score_threshold,
            rag_min_margin=self._settings.auto_reply_rag_min_margin,
        )
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
                if self._shop_id is None:
                    self._shop = await self._repository.ensure_shop(
                        self._settings.default_shop_name
                    )
                    self._shop_id = str(self._shop["id"])
                else:
                    self._shop = await self._repository.get_shop(self._shop_id)
                    if self._shop is None:
                        raise LookupError("店铺不存在")
                expired_assets = await self._repository.cleanup(
                    self._settings.message_retention_days,
                    self._settings.audit_retention_days,
                )
                for storage_key in expired_assets:
                    try:
                        self._asset_storage.delete(storage_key)
                    except Exception:
                        logger.exception("message_asset_cleanup_failed", extra={"event": "message_asset_cleanup_failed"})
                self._worker = create_log_task(
                    self._send_worker(),
                    name="console-send-worker",
                    shop_id=self._shop_id,
                )
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
        assert self._shop_id is not None
        event_payload = self._sanitize_event_payload(event_type, payload)
        event = await self._repository.add_event(
            event_type, event_payload, shop_id=self._shop_id
        )
        await self._broker.publish(event)
        return event

    @staticmethod
    def _sanitize_event_payload(
        event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """SSE 事件只持久化刷新所需字段，避免把聊天正文保存 90 天。"""
        fields = {
            "conversation.upserted": {
                "id",
                "shop_id",
                "state",
                "unread_count",
                "response_started_at",
                "response_deadline_at",
            },
            "message.created": {
                "id",
                "shop_id",
                "conversation_id",
                "direction",
                "kind",
                "occurred_at",
            },
            "outbound.updated": {
                "id",
                "shop_id",
                "conversation_id",
                "source",
                "status",
                "error_code",
            },
            "reply.decision": {
                "id",
                "shop_id",
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
        self._accepting_work = True
        try:
            current = await self._connector.status()
            if current.status in {ConnectorStatus.DEGRADED, ConnectorStatus.ERROR}:
                await self._connector.stop()
            snapshot = await self._connector.start(
                self._on_message, self._on_connector_status, self._on_profile
            )
            if snapshot.status == ConnectorStatus.READY:
                await self._recover_jobs()
        except ConnectorError as exc:
            raise ConsoleUnavailableError(str(exc)) from exc
        return self._status_dict(snapshot)

    async def stop_connector(self) -> dict[str, Any]:
        await self.ensure_initialized()
        self._jobs_recovered = False
        return self._status_dict(await self._connector.stop())

    async def focus_connector(self) -> dict[str, Any]:
        await self.ensure_initialized()
        try:
            await self._connector.focus()
        except ConnectorError as exc:
            raise ConsoleUnavailableError(str(exc)) from exc
        return await self.connector_status()

    async def prepare_offline(self) -> int:
        await self.ensure_initialized()
        assert self._shop_id is not None
        self._accepting_work = False
        for task in self._debounces.values():
            task.cancel()
        await asyncio.gather(*self._debounces.values(), return_exceptions=True)
        self._debounces.clear()
        self._batches.clear()
        cancelled = await self._repository.cancel_queued_jobs(self._shop_id)
        try:
            await asyncio.wait_for(self._send_queue.join(), timeout=10)
        except TimeoutError:
            logger.warning(
                "shop_send_drain_timeout",
                extra={"event": "shop_send_drain_timeout", "shop_id": self._shop_id},
            )
        return cancelled

    async def connector_status(self) -> dict[str, Any]:
        return self._status_dict(await self._connector.status())

    async def connector_diagnostics(self) -> dict[str, object]:
        return await self._connector.diagnostics()

    async def message_asset(self, asset_id: str) -> tuple[str, str]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        item = await self._repository.get_message_asset(asset_id, self._shop_id)
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
        if snapshot.status in {ConnectorStatus.READY, ConnectorStatus.DEGRADED}:
            identity = await self._connector.identity()
            if identity is not None and self._identity_handler is not None:
                await self._identity_handler(identity)
        if snapshot.status == ConnectorStatus.READY:
            await self._recover_jobs()
        if self._runtime_status_handler is not None:
            await self._runtime_status_handler(snapshot)

    async def _recover_jobs(self) -> None:
        if self._jobs_recovered or self._shop_id is None:
            return
        for job in await self._repository.recover_queued_jobs(self._shop_id):
            self._send_queue.put_nowait(str(job["id"]))
        self._jobs_recovered = True

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
            self._shop["id"],
            payload,
            response_timeout_seconds=self._settings.pdd_response_timeout_seconds,
        )
        if not inserted:
            return
        await self._emit("conversation.upserted", conversation)
        await self._emit("message.created", {**stored, "conversation_id": conversation["id"]})
        if not self._accepting_work:
            return
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
                shop_id=self._shop_id,
            )
            await self._record_knowledge_gap(
                message.kind,
                conversation=conversation,
                reason_code="UNSUPPORTED_MESSAGE",
            )
            await self._emit("reply.decision", {**decision, "conversation_id": conversation["id"]})
            return
        conversation_id = str(conversation["id"])
        self._batches.setdefault(conversation_id, []).append(message)
        previous = self._debounces.pop(conversation_id, None)
        if previous:
            previous.cancel()
        self._debounces[conversation_id] = create_log_task(
            self._evaluate_after_delay(conversation_id),
            name=f"auto-reply-{conversation_id}",
            shop_id=self._shop_id,
            conversation_id=conversation_id,
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
        if self._pdd_runtime_mode == "graph":
            await self._evaluate_batch_graph(conversation_id, batch)
            return
        await self._evaluate_batch_legacy(conversation_id, batch)

    async def _evaluate_batch_graph(
        self, conversation_id: str, batch: list[BrowserMessage]
    ) -> None:
        """Run PDD AI decisions through the injected shared Agent Graph."""
        conversation = await self._repository.get_conversation(
            conversation_id, self._shop_id
        )
        if conversation is None:
            return
        assert self._shop is not None
        shop = await self._repository.get_shop(self._shop["id"])
        status = await self._connector.status()
        allow_auto = bool(
            shop
            and shop["global_auto_reply_enabled"]
            and (
                shop["reception_mode"] == "guarded_auto"
                or self._legacy_single_shop_mode
            )
            and conversation["auto_reply_enabled"]
            and status.status == ConnectorStatus.READY
        )
        query = "\n".join(message.content or "" for message in batch[-5:]).strip()
        batch_key = sha256("|".join(message.fingerprint for message in batch).encode()).hexdigest()
        blockers: list[str] = []
        if not shop or not shop["global_auto_reply_enabled"]:
            blockers.append("全局自动接待未开启")
        if not self._legacy_single_shop_mode and (
            not shop or shop["reception_mode"] != "guarded_auto"
        ):
            blockers.append("当前为人机协同模式")
        if not conversation["auto_reply_enabled"]:
            blockers.append("当前会话为人工接待")
        if status.status != ConnectorStatus.READY:
            blockers.append("拼多多连接器未就绪")

        assert self._pdd_agent_runtime is not None
        try:
            state = await invoke_pdd_agent(
                self._pdd_agent_runtime,
                conversation_id=conversation_id,
                shop_id=str(shop["id"]) if shop else "",
                customer_id=str(conversation.get("customer_id") or "") or None,
                batch=batch,
                goods_id=str(conversation.get("goods_id") or "") or None,
                goods_name=str(conversation.get("goods_name") or "") or None,
                conversation=conversation,
                shop=shop,
            )
            handoff = await self._repository.get_handoff_config(str(shop["id"]))
            policy = evaluate_pdd_channel_policy(
                state,
                conversation,
                allow_auto=allow_auto,
                auto_reply_policy=self._policy,
                product_answer_service=self._product_answer_service,
                settings=self._settings,
                auto_reply_blockers=blockers,
                handoff_reply=str(handoff["reply_template"]),
            )
        except Exception:
            # A graph failure is terminal for this batch. Never re-run Legacy AI,
            # which could duplicate model calls or side effects.
            logger.exception(
                "pdd_agent_graph_failed",
                extra={"event": "pdd_agent_graph_failed"},
            )
            policy = PDDChannelDecision(
                route="error",
                action="suggest",
                answer=None,
                reason="Agent Graph 暂不可用，已生成人工建议",
                reason_code="AGENT_GRAPH_UNAVAILABLE",
            )

        if policy.action == "handoff":
            await self._handoff(
                conversation_id=conversation_id,
                shop_id=str(shop["id"]) if shop else "",
                batch_key=batch_key,
                reason=policy.reason or "当前问题已转人工处理",
                send_reply=bool(policy.answer),
                product_id=policy.product_id,
                product_name=policy.product_name,
            )
            if policy.reason_code:
                await self._record_knowledge_gap(
                    query,
                    conversation=conversation,
                    reason_code=policy.reason_code,
                )
            return

        decision_payload = {
            "batch_key": batch_key,
            "route": policy.route,
            "action": policy.action,
            "qa_code": policy.qa_code,
            "product_id": policy.product_id,
            "product_name": policy.product_name,
            "greeting_type": policy.greeting_type,
            "recognition_source": policy.recognition_source,
            "top_score": policy.top_score,
            "score_margin": policy.score_margin,
            "risk_reason": policy.reason,
            "suggested_answer": policy.answer,
            "policy_version": self._settings.auto_reply_policy_version,
        }
        decision = await self._repository.add_decision(
            conversation_id, decision_payload, shop_id=self._shop_id
        )
        if policy.reason_code in KNOWLEDGE_GAP_REASON_CODES or policy.reason_code in {
            "PRODUCT_DATA_MISSING",
            "PRODUCT_CONTEXT_MISSING",
            "AGENT_GRAPH_UNAVAILABLE",
        }:
            await self._record_knowledge_gap(
                query,
                conversation=conversation,
                reason_code=policy.reason_code or "fallback",
            )
        await self._emit("reply.decision", {**decision, "conversation_id": conversation_id})
        if decision.get("action") == "auto_send" and decision.get("suggested_answer"):
            job, created = await self._repository.create_outbound_job(
                conversation_id,
                client_request_id=f"auto:{batch_key[:59]}",
                source="auto",
                content=str(decision["suggested_answer"])[:400],
            )
            if created:
                await self._emit("outbound.updated", job)
                self._send_queue.put_nowait(str(job["id"]))

    async def _evaluate_batch_legacy(
        self, conversation_id: str, batch: list[BrowserMessage]
    ) -> None:
        conversation = await self._repository.get_conversation(
            conversation_id, self._shop_id
        )
        if conversation is None:
            return
        assert self._shop is not None
        shop = await self._repository.get_shop(self._shop["id"])
        status = await self._connector.status()
        allow_auto = bool(
            shop
            and shop["global_auto_reply_enabled"]
            and (
                shop["reception_mode"] == "guarded_auto"
                or self._legacy_single_shop_mode
            )
            and conversation["auto_reply_enabled"]
            and status.status == ConnectorStatus.READY
        )
        query = "\n".join(message.content or "" for message in batch[-5:]).strip()
        batch_key = sha256("|".join(message.fingerprint for message in batch).encode()).hexdigest()
        decision_payload: dict[str, Any] | None = None
        knowledge_gap_reason_code: str | None = None

        if self._router.requires_handoff(query):
            await self._handoff(
                conversation_id=conversation_id,
                shop_id=str(shop["id"]) if shop else "",
                batch_key=batch_key,
                reason="售后或争议问题已转人工处理",
                send_reply=True,
                product_id=str(conversation.get("goods_id") or "") or None,
                product_name=str(conversation.get("goods_name") or "") or None,
            )
            return

        try:
            greeting_config = await self._repository.get_greeting_config(str(shop["id"]))
        except Exception:
            logger.exception(
                "greeting_config_load_failed",
                extra={"event": "greeting_config_load_failed"},
            )
            greeting_config = {
                "enabled": True,
                "trigger_groups": default_trigger_groups(),
                "reply_templates": default_reply_templates(),
            }
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
                greeting_config=greeting_config,
            )
        except Exception:
            # 意图模型不可用时保留既有 QA/RAG 降级路径，不让新增分支阻断接待。
            logger.exception(
                "agent_intent_routing_failed",
                extra={"event": "agent_intent_routing_failed"},
            )
        else:
            if agent_reply.intent == "daily_greeting" and agent_reply.answer:
                blockers: list[str] = []
                if not shop or not shop["global_auto_reply_enabled"]:
                    blockers.append("全局自动接待未开启")
                if (
                    not self._legacy_single_shop_mode
                    and (not shop or shop["reception_mode"] != "guarded_auto")
                ):
                    blockers.append("当前为人机协同模式")
                if not conversation["auto_reply_enabled"]:
                    blockers.append("当前会话为人工接待")
                if status.status != ConnectorStatus.READY:
                    blockers.append("拼多多连接器未就绪")
                source_label = (
                    "规则" if agent_reply.recognition_source == "rule" else "模型"
                )
                decision_payload = {
                    "batch_key": batch_key,
                    "route": "greeting",
                    "action": "auto_send" if allow_auto else "suggest",
                    "greeting_type": agent_reply.greeting_type,
                    "recognition_source": agent_reply.recognition_source,
                    "top_score": agent_reply.confidence,
                    "risk_reason": (
                        f"{source_label}识别为一般问候，已进入自动发送队列"
                        if allow_auto
                        else f"{source_label}识别为一般问候；{'；'.join(blockers)}，仅生成建议"
                    ),
                    "suggested_answer": agent_reply.answer,
                    "policy_version": self._settings.auto_reply_policy_version,
                }

        if decision_payload is None:
            social_decision = await self._social_router.classify(query)
            if social_decision.intent != "other" and social_decision.response:
                decision_payload = {
                    "batch_key": batch_key,
                    "route": social_decision.intent,
                    "action": "auto_send" if allow_auto else "suggest",
                    "recognition_source": social_decision.source,
                    "top_score": social_decision.confidence,
                    "risk_reason": (
                        f"识别为{'社交' if social_decision.intent == 'small_talk' else '情绪'}表达，"
                        "已进入自动发送队列"
                        if allow_auto
                        else "识别为社交或情绪表达；未满足自动发送条件，仅生成建议"
                    ),
                    "suggested_answer": social_decision.response,
                    "policy_version": self._settings.auto_reply_policy_version,
                }

        if decision_payload is None:
            try:
                qa_service = await self._qa_provider()
                faq_result = qa_service.match_exact(
                    query,
                    product_code=str(conversation.get("goods_id") or "") or None,
                    product_name=str(conversation.get("goods_name") or "") or None,
                )
                if faq_result is not None:
                    policy = self._policy.evaluate(
                        query, faq_result, conversation, allow_auto=allow_auto
                    )
                    if policy.reason_code in KNOWLEDGE_GAP_REASON_CODES:
                        knowledge_gap_reason_code = policy.reason_code
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
                else:
                    classification = await self._router.classify(
                        query,
                        has_product_context=bool(conversation.get("goods_id")),
                    )
                    if classification.route == "product":
                        goods_id = str(conversation.get("goods_id") or "").strip()
                        if not goods_id:
                            await self._handoff(
                                conversation_id=conversation_id,
                                shop_id=str(shop["id"]) if shop else "",
                                batch_key=batch_key,
                                reason="商品咨询缺少可确认的商品卡片",
                                send_reply=False,
                            )
                            await self._record_knowledge_gap(
                                query,
                                conversation=conversation,
                                reason_code="PRODUCT_CONTEXT_MISSING",
                            )
                            return
                        try:
                            product = await self._product_provider.get_product(goods_id)
                            product_answer = await self._product_answer_service.answer(
                                query, product
                            )
                        except ProductLookupError:
                            await self._handoff(
                                conversation_id=conversation_id,
                                shop_id=str(shop["id"]) if shop else "",
                                batch_key=batch_key,
                                reason="商品资料不可用或与会话商品不一致",
                                send_reply=False,
                                product_id=goods_id,
                                product_name=str(conversation.get("goods_name") or "") or None,
                            )
                            await self._record_knowledge_gap(
                                query,
                                conversation=conversation,
                                reason_code="PRODUCT_DATA_MISSING",
                            )
                            return
                        except Exception:
                            logger.exception("product_answer_failed", extra={"event": "product_answer_failed"})
                            await self._handoff(
                                conversation_id=conversation_id,
                                shop_id=str(shop["id"]) if shop else "",
                                batch_key=batch_key,
                                reason="商品咨询暂无法安全回答，已转人工",
                                send_reply=False,
                                product_id=goods_id,
                                product_name=str(conversation.get("goods_name") or "") or None,
                            )
                            return
                        auto_send = self._product_answer_service.can_auto_send(
                            product_answer, allow_auto=allow_auto, settings=self._settings
                        )
                        reason = None if auto_send else "商品回答需要人工确认"
                        if not product_answer.facts_supported:
                            knowledge_gap_reason_code = "PRODUCT_DATA_MISSING"
                        elif product_answer.needs_clarification:
                            knowledge_gap_reason_code = "PRODUCT_CONTEXT_MISSING"
                        elif (
                            product_answer.confidence
                            < self._settings.product_auto_reply_min_confidence
                        ):
                            knowledge_gap_reason_code = "LOW_PRODUCT_CONFIDENCE"
                        decision_payload = {
                            "batch_key": batch_key,
                            "route": "product",
                            "action": "auto_send" if auto_send else "suggest",
                            "product_id": product.id,
                            "product_name": product.name,
                            "top_score": product_answer.confidence,
                            "risk_reason": reason,
                            "suggested_answer": product_answer.answer,
                            "policy_version": self._settings.auto_reply_policy_version,
                        }
                    else:
                        result = await qa_service.answer_rag(
                            query,
                            product_code=str(conversation.get("goods_id") or "") or None,
                            product_name=str(conversation.get("goods_name") or "") or None,
                        )
                        policy = self._policy.evaluate(
                            query, result, conversation, allow_auto=allow_auto
                        )
                        if policy.reason_code in KNOWLEDGE_GAP_REASON_CODES:
                            knowledge_gap_reason_code = policy.reason_code
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
            conversation_id, decision_payload, shop_id=self._shop_id
        )
        if knowledge_gap_reason_code is not None:
            await self._record_knowledge_gap(
                query,
                conversation=conversation,
                reason_code=knowledge_gap_reason_code,
            )
        await self._emit("reply.decision", {**decision, "conversation_id": conversation_id})
        if decision.get("action") == "auto_send" and decision.get("suggested_answer"):
            job, created = await self._repository.create_outbound_job(
                conversation_id,
                # outbound_jobs.client_request_id 最长 64；保留 59 位哈希仍有
                # 236 bit 幂等空间，同时避免 MySQL 因 5 位前缀溢出而拒绝入队。
                client_request_id=f"auto:{batch_key[:59]}",
                source="auto",
                content=str(decision["suggested_answer"])[:400],
            )
            if created:
                await self._emit("outbound.updated", job)
                self._send_queue.put_nowait(str(job["id"]))

    async def _handoff(
        self,
        *,
        conversation_id: str,
        shop_id: str,
        batch_key: str,
        reason: str,
        send_reply: bool,
        product_id: str | None = None,
        product_name: str | None = None,
    ) -> None:
        """审计人工接管并在售后路径按店铺配置发送固定说明。"""
        reply_template: str | None = None
        if send_reply:
            config = await self._repository.get_handoff_config(shop_id)
            reply_template = str(config["reply_template"])
        decision = await self._repository.add_decision(
            conversation_id,
            {
                "batch_key": batch_key,
                "route": "handoff",
                "action": "handoff",
                "product_id": product_id,
                "product_name": product_name,
                "risk_reason": reason,
                "suggested_answer": reply_template,
                "policy_version": self._settings.auto_reply_policy_version,
            },
            shop_id=self._shop_id,
        )
        await self._emit("reply.decision", {**decision, "conversation_id": conversation_id})
        conversation, job, created = await self._repository.handoff_conversation(
            conversation_id,
            client_request_id=f"handoff:{batch_key[:56]}",
            content=reply_template,
        )
        await self._emit("conversation.upserted", conversation)
        if job is not None and created:
            await self._emit("outbound.updated", job)
            self._send_queue.put_nowait(str(job["id"]))

    async def _record_knowledge_gap(
        self,
        query: str,
        *,
        conversation: dict[str, Any],
        reason_code: str,
    ) -> None:
        assert self._shop_id is not None
        normalized = " ".join(query.strip().lower().split())[:500]
        if not normalized:
            return
        try:
            gap = await self._repository.record_knowledge_gap(
                self._shop_id,
                product_id=str(conversation.get("goods_id") or "") or None,
                normalized_question=normalized,
                example_question=query[:4000],
                reason_code=reason_code[:64],
            )
            await self._emit("knowledge_gap.updated", gap)
        except Exception:
            logger.exception(
                "knowledge_gap_record_failed",
                extra={"event": "knowledge_gap_record_failed"},
            )

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
                    job = await self._repository.get_job(job_id, self._shop_id)
                    if job and job["status"] == "sending":
                        job = await self._repository.update_job(
                            job_id,
                            status="uncertain",
                            error_code="WORKER_UNCERTAIN",
                            shop_id=self._shop_id,
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
        job = await self._repository.get_job(job_id, self._shop_id)
        if not job or job["status"] != "queued":
            return
        conversation = await self._repository.get_conversation(
            str(job["conversation_id"]), self._shop_id
        )
        if conversation is None:
            return
        job = await self._repository.update_job(
            job_id, status="sending", shop_id=self._shop_id
        )
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
                shop_id=self._shop_id,
            )
            await self._pause_after_send_failure(job)
        except ConnectorError:
            job = await self._repository.update_job(
                job_id,
                status="failed",
                error_code="CONNECTOR_ERROR",
                shop_id=self._shop_id,
            )
            await self._pause_after_send_failure(job)
        else:
            job = await self._repository.update_job(
                job_id,
                status="sent",
                clicked_at=receipt.clicked_at,
                sent_at=receipt.confirmed_at,
                responder_name=receipt.responder_name,
                shop_id=self._shop_id,
            )
        await self._emit("outbound.updated", job)

    async def _pause_after_send_failure(self, job: dict[str, Any]) -> None:
        conversation_id = str(job.get("conversation_id") or "")
        if not conversation_id:
            return
        try:
            conversation = await self._repository.set_conversation_automation(
                conversation_id, False, self._shop_id
            )
        except LookupError:
            logger.exception(
                "failed_conversation_pause_failed",
                extra={"event": "failed_conversation_pause_failed"},
            )
            return
        await self._emit("conversation.upserted", conversation)

    async def list_conversations(
        self, *, limit: int, state: str | None, search: str | None, before: datetime | None
    ) -> list[dict[str, Any]]:
        await self.ensure_initialized()
        assert self._shop is not None
        return await self._repository.list_conversations(
            self._shop["id"], limit=limit, state=state, search=search, before=before
        )

    async def shop_summary(self) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        item = await self._repository.get_shop(self._shop_id)
        if item is None:
            raise ResourceNotFoundError("店铺不存在")
        self._shop = item
        return item

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
        assert self._shop_id is not None
        return await self._repository.list_customer_messages(
            customer_id,
            message_date=message_date,
            limit=limit,
            shop_id=self._shop_id,
        )

    async def conversation_detail(
        self, conversation_id: str, *, limit: int, before: datetime | None
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        conversation = await self._repository.get_conversation(
            conversation_id, self._shop_id
        )
        if conversation is None:
            raise ResourceNotFoundError("会话不存在")
        return {
            "conversation": conversation,
            "messages": await self._repository.list_messages(
                conversation_id,
                limit=limit,
                before=before,
                shop_id=self._shop_id,
            ),
            "decision": await self._repository.latest_decision(
                conversation_id, self._shop_id
            ),
        }

    async def mark_conversation_read(self, conversation_id: str) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        try:
            return await self._repository.mark_conversation_read(
                conversation_id, self._shop_id
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc

    async def reply(
        self, conversation_id: str, *, content: str, client_request_id: str
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        if not self._accepting_work:
            raise ConsoleUnavailableError("店铺正在下线，暂不接受新发送任务")
        status = await self._connector.status()
        if status.status != ConnectorStatus.READY:
            raise ConsoleUnavailableError("拼多多连接器未就绪")
        if await self._repository.get_conversation(
            conversation_id, self._shop_id
        ) is None:
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
                conversation_id, enabled, self._shop_id
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        await self._emit("conversation.upserted", conversation)
        return conversation

    async def clear_conversation_response_timer(
        self, conversation_id: str
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        try:
            conversation = await self._repository.clear_conversation_response_timer(
                conversation_id, self._shop_id
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

    async def greeting_automation(self) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop is not None
        try:
            return await self._repository.get_greeting_config(self._shop["id"])
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc

    async def set_greeting_automation(
        self,
        *,
        enabled: bool,
        trigger_groups: dict[str, list[str]],
        reply_templates: dict[str, list[str]],
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop is not None
        try:
            result = await self._repository.update_greeting_config(
                self._shop["id"],
                enabled=enabled,
                trigger_groups=trigger_groups,
                reply_templates=reply_templates,
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        await self._emit(
            "greeting.automation.updated", {"enabled": result["enabled"]}
        )
        return result

    async def handoff_automation(self) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop is not None
        return await self._repository.get_handoff_config(self._shop["id"])

    async def set_handoff_automation(self, reply_template: str) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop is not None
        result = await self._repository.update_handoff_config(
            self._shop["id"], reply_template=reply_template
        )
        await self._emit("handoff.automation.updated", {"updated_at": result["updated_at"]})
        return result

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

    async def set_reception_mode(self, mode: str) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        try:
            result = await self._repository.update_shop_state(
                self._shop_id, reception_mode=mode
            )
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        self._shop = result
        await self._emit("shop.updated", result)
        return result

    async def customer_note(self, customer_id: str) -> dict[str, Any] | None:
        await self.ensure_initialized()
        assert self._shop_id is not None
        try:
            return await self._repository.get_customer_note(
                self._shop_id, customer_id
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc

    async def set_customer_note(
        self,
        customer_id: str,
        *,
        content: str,
        tags: list[str],
        pinned: bool,
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        try:
            result = await self._repository.update_customer_note(
                self._shop_id,
                customer_id,
                content=content,
                tags=tags,
                pinned=pinned,
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        await self._emit("customer.note.updated", result)
        return result

    async def knowledge_gaps(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        return await self._repository.list_knowledge_gaps(
            self._shop_id, status=status, limit=limit
        )

    async def update_knowledge_gap(
        self,
        gap_id: str,
        *,
        status: str,
        candidate_answer: str | None = None,
        linked_qa_code: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        try:
            result = await self._repository.update_knowledge_gap(
                self._shop_id,
                gap_id,
                status=status,
                candidate_answer=candidate_answer,
                linked_qa_code=linked_qa_code,
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        await self._emit("knowledge_gap.updated", result)
        return result

    async def create_knowledge_gap_qa_draft(
        self, gap_id: str, *, standard_answer: str
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        try:
            result = await self._repository.create_knowledge_gap_qa_draft(
                self._shop_id,
                gap_id,
                standard_answer=standard_answer,
            )
        except LookupError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        await self._emit("knowledge_gap.updated", result)
        return result

    async def stats(self, *, days: int = 7) -> dict[str, Any]:
        await self.ensure_initialized()
        assert self._shop_id is not None
        return await self._repository.shop_stats(
            self._shop_id, since=datetime.now(timezone.utc) - timedelta(days=days)
        )
