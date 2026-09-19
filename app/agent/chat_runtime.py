"""Unified Chat 的 State Contract v2 生命周期与数据映射。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from app.agent.checkpoint import CheckpointStore
from app.agent.coordinator import StateCoordinator
from app.agent.state import (
    AgentState,
    ChatRoute,
    ChatSessionState,
    ChatTurnState,
    HumanState,
    ProductReference,
    ProductResolutionState,
    ResolvedProductContext,
    RetrievalEvidence,
    RetrievalState,
    StateError,
    StateMessage,
    WorkflowState,
    WorkflowStatus,
    utcnow,
)
from app.qa.models import QAResult, RetrievalDocument
from app.services.product_service import ProductProfile

if TYPE_CHECKING:
    from app.rules.registry import RuleEvaluationResult
    from app.schemas.chat import ChatResponse


logger = logging.getLogger(__name__)
CHAT_TURN_NODE = "chat_turn"

_PRODUCT_SOURCE_MAP = {
    "request": "customer_selected",
    "url": "message_extraction",
    "message_id": "message_extraction",
    "name_exact": "message_extraction",
    "history_semantic": "message_extraction",
    # MySQL 绑定没有保存最初识别来源，恢复时使用中性的手工引用语义。
    "conversation": "manual",
}


def product_reference_from_resolution(
    product_id: str,
    source: str,
    *,
    confidence: float | None = None,
) -> ProductReference:
    """把 ProductResolver 来源收敛为稳定的 Session 引用来源。"""
    return ProductReference(
        product_id=product_id,
        source=_PRODUCT_SOURCE_MAP.get(source, "manual"),
        confidence=confidence,
    )


def product_profile_to_state_context(
    product: ProductProfile,
) -> ResolvedProductContext:
    """把 PostgreSQL 商品事实显式映射为当前 Turn 的快照。"""
    return ResolvedProductContext(
        product_id=product.id,
        name=product.name,
        category=product.category_name,
        summary=product.summary,
        selling_points=list(product.selling_points),
        specifications=dict(product.specifications),
        usage=_optional_text_list(product.usage),
        suitable_for=_optional_text_list(product.suitable_for),
        warnings=_optional_text_list(product.warnings),
        after_sales_limits=_optional_text_list(product.after_sales_limits),
        source_updated_at=_parse_iso_datetime(product.updated_at),
    )


def retrieval_document_to_state_evidence(
    document: RetrievalDocument,
) -> RetrievalEvidence:
    """保留真实检索轨迹，并提供稳定的 document identity fallback。"""
    metadata = dict(document.metadata)
    document_id = str(metadata.get("document_id") or document.chunk_id)
    product_id = metadata.get("product_id")
    return RetrievalEvidence(
        document_id=document_id,
        chunk_id=document.chunk_id,
        content=document.content,
        source=document.source,
        product_id=str(product_id) if product_id is not None else None,
        dense_score=_non_negative(document.dense_score),
        bm25_score=_non_negative(document.bm25_score),
        fusion_score=_non_negative(document.fusion_score),
        rerank_score=_non_negative(document.rerank_score),
        metadata=metadata,
    )


class ChatStateRuntime:
    """记录聊天主链路状态，不参与 Rule、Product 或 QA 业务决策。"""

    def __init__(
        self,
        checkpoint_store: CheckpointStore | None = None,
        *,
        cleanup_completed: bool = True,
    ) -> None:
        self._store = checkpoint_store
        self._coordinator = (
            StateCoordinator(checkpoint_store) if checkpoint_store is not None else None
        )
        self._cleanup_completed = cleanup_completed

    def create_state(
        self,
        *,
        message: str,
        conversation_id: str | None,
        customer_id: str | None,
        service_stage: str | None,
    ) -> AgentState:
        run_id = str(uuid4())
        turn_id = str(uuid4())
        session_id = conversation_id or f"chat-{uuid4()}"
        workflow = WorkflowState(run_id=run_id, next_node=CHAT_TURN_NODE)
        session = ChatSessionState(
            session_id=session_id,
            thread_id=session_id,
            channel="other",
            customer_id=customer_id,
            service_stage=service_stage or "general",
            workflow=workflow,
        )
        input_message = StateMessage(
            role="customer",
            content=message,
            session_id=session_id,
            turn_id=turn_id,
        )
        session.short_term_memory.recent_message_ids.append(input_message.id)
        session.short_term_memory.last_user_message_id = input_message.id
        turn = ChatTurnState(
            turn_id=turn_id,
            session_id=session_id,
            run_id=run_id,
            input_message_id=input_message.id,
            original_query=message,
            workflow=workflow,
        )
        state = AgentState(
            run_id=run_id,
            thread_id=session_id,
            conversation_id=conversation_id,
            session=session,
            turn=turn,
            messages=[input_message],
            next_node=CHAT_TURN_NODE,
        )
        state.sync_contract_state()
        return state

    async def start(self, state: AgentState) -> None:
        if self._coordinator is not None:
            await self._coordinator.create(state)
        state.begin_node(CHAT_TURN_NODE)
        if self._store is not None:
            await self._store.save(state, reason=f"before:{CHAT_TURN_NODE}")

    def hydrate_product_binding(self, state: AgentState, product_id: str) -> None:
        session = _require_session(state)
        session.current_product = product_reference_from_resolution(
            product_id,
            "conversation",
        )
        _remember_product(session, product_id)

    def clear_product_binding(self, state: AgentState) -> None:
        session = _require_session(state)
        stale_product_id = (
            session.current_product.product_id
            if session.current_product is not None
            else None
        )
        session.current_product = None
        if stale_product_id is not None:
            session.recent_product_ids = [
                product_id
                for product_id in session.recent_product_ids
                if product_id != stale_product_id
            ]

    def record_rule(
        self,
        state: AgentState,
        result: RuleEvaluationResult,
    ) -> None:
        decision = result.terminal_decision
        if decision is None and result.decisions:
            decision = result.decisions[0]
        if decision is not None:
            state.intent = decision.reason_code or decision.rule_name
        if result.terminal_decision is not None:
            state.route = result.terminal_decision.route
            if result.terminal_decision.route == "fallback":
                _require_turn(state).fallback_reason = result.terminal_decision.reason_code
        if result.requires_human:
            reason_code = decision.reason_code if decision is not None else "human_required"
            human = HumanState(
                required=True,
                status="requested",
                reason_code=reason_code,
                requested_at=utcnow(),
            )
            _require_session(state).human = human
            _require_turn(state).human = human
            state.risk_level = "high"
            if reason_code and reason_code not in state.risk_reasons:
                state.risk_reasons.append(reason_code)
        state.sync_contract_state()

    def record_product_pending(
        self,
        state: AgentState,
        *,
        product_id: str,
        source: str,
    ) -> None:
        reference = product_reference_from_resolution(product_id, source)
        _require_turn(state).product = ProductResolutionState(
            reference=reference,
            status="pending",
        )

    def record_product_resolved(
        self,
        state: AgentState,
        product: ProductProfile,
        *,
        source: str,
    ) -> None:
        reference = product_reference_from_resolution(product.id, source)
        session = _require_session(state)
        session.current_product = reference
        _remember_product(session, product.id)
        turn = _require_turn(state)
        turn.product = ProductResolutionState(
            reference=reference,
            status="resolved",
            context=product_profile_to_state_context(product),
        )
        state.route = "product"
        state.sync_contract_state()

    def record_qa(self, state: AgentState, result: QAResult) -> None:
        turn = _require_turn(state)
        counts = result.retrieval_counts
        candidates = (
            [retrieval_document_to_state_evidence(item) for item in result.trace_documents]
            if result.route == "rag"
            else []
        )
        top_score = _top_score(candidates)
        turn.retrieval = RetrievalState(
            candidates=candidates,
            dense_count=max(0, counts.get("dense", 0)),
            bm25_count=max(0, counts.get("bm25", 0)),
            fusion_count=max(0, counts.get("fusion", 0)),
            rerank_count=max(0, counts.get("rerank", 0)),
            top_score=top_score,
            evidence_sufficient=result.route == "rag" and bool(candidates),
        )
        state.evidence = list(candidates)
        state.route = result.route
        if result.route == "fallback":
            turn.fallback_reason = "qa_no_evidence"
        state.sync_contract_state()

    async def complete(self, state: AgentState, response: ChatResponse) -> None:
        self._record_response(state, response)
        turn = _require_turn(state)
        session = _require_session(state)
        output_message = StateMessage(
            role="assistant",
            content=response.answer,
            session_id=session.session_id,
            turn_id=turn.turn_id,
            reply_to_message_id=turn.input_message_id,
        )
        state.messages.append(output_message)
        turn.output_message_id = output_message.id
        session.short_term_memory.recent_message_ids.append(output_message.id)
        session.short_term_memory.last_assistant_message_id = output_message.id
        state.final_answer = response.answer
        turn.completed_at = utcnow()
        state.complete_node(CHAT_TURN_NODE)
        if self._store is not None:
            await self._store.save(state, reason=f"after:{CHAT_TURN_NODE}")
            if self._cleanup_completed:
                try:
                    await self._store.delete(state.run_id)
                except Exception:
                    logger.exception(
                        "chat_completed_checkpoint_cleanup_failed",
                        extra={
                            "event": "chat_completed_checkpoint_cleanup_failed",
                            "run_id": state.run_id,
                        },
                    )

    async def fail(self, state: AgentState, error: Exception) -> None:
        error_code = type(error).__name__.upper()
        turn = _require_turn(state)
        turn.fallback_reason = "chat_processing_failed"
        if state.current_node == CHAT_TURN_NODE:
            state.fail_node(
                CHAT_TURN_NODE,
                code=error_code,
                retryable=True,
                message="聊天请求处理失败",
            )
        else:
            state.status = WorkflowStatus.FAILED
            state.resume_from = CHAT_TURN_NODE
            state.error = StateError(
                node=CHAT_TURN_NODE,
                code=error_code,
                message="聊天请求处理失败",
                retryable=True,
            )
            state.sync_contract_state()
        if self._store is not None:
            await self._store.save(state, reason=f"failed:{CHAT_TURN_NODE}")

    def _record_response(self, state: AgentState, response: ChatResponse) -> None:
        turn = _require_turn(state)
        route = _response_state_route(response)
        state.route = route
        if response.source == "product_selection":
            turn.product = ProductResolutionState(status="pending")
        elif response.source == "product" and response.route != "product":
            product_status = (
                "not_found" if response.route == "product_not_found" else "failed"
            )
            turn.product = ProductResolutionState(
                status=product_status,
                error_code=response.route,
            )
            turn.fallback_reason = response.route
        state.sync_contract_state()


def _response_state_route(response: ChatResponse) -> ChatRoute:
    if response.source == "product_selection":
        return "product_selection"
    if response.source == "product":
        return "product"
    if response.route in {
        "faq",
        "rag",
        "human",
        "greeting",
        "small_talk",
        "empathy",
        "llm_fallback",
        "fallback",
        "tool",
    }:
        return response.route
    return "fallback"


def _optional_text_list(value: str | None) -> list[str]:
    return [value] if value else []


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _non_negative(value: float | None) -> float | None:
    return value if value is not None and value >= 0 else None


def _top_score(candidates: list[RetrievalEvidence]) -> float | None:
    if not candidates:
        return None
    first = candidates[0]
    return next(
        (
            score
            for score in (
                first.rerank_score,
                first.fusion_score,
                first.dense_score,
                first.bm25_score,
            )
            if score is not None
        ),
        None,
    )


def _remember_product(session: ChatSessionState, product_id: str) -> None:
    if product_id not in session.recent_product_ids:
        session.recent_product_ids.append(product_id)


def _require_session(state: AgentState) -> ChatSessionState:
    if state.session is None:
        raise ValueError("Chat State 缺少 session")
    return state.session


def _require_turn(state: AgentState) -> ChatTurnState:
    if state.turn is None:
        raise ValueError("Chat State 缺少 turn")
    return state.turn
