"""Unified Chat 的 State Contract v2 生命周期与数据映射。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from app.agent.checkpoint import CheckpointStore
from app.agent.state import (
    AgentState,
    ChatRoute,
    ChatSessionState,
    ChatTurnState,
    ProductResolutionState,
    StateError,
    StateMessage,
    WorkflowState,
    WorkflowStatus,
    utcnow,
)

if TYPE_CHECKING:
    from app.schemas.chat import ChatResponse


logger = logging.getLogger(__name__)
class ChatStateRuntime:
    """记录聊天主链路状态，不参与 Rule、Product 或 QA 业务决策。"""

    def __init__(
        self,
        checkpoint_store: CheckpointStore | None = None,
        *,
        cleanup_completed: bool = True,
    ) -> None:
        self._store = checkpoint_store
        self._cleanup_completed = cleanup_completed

    def create_state(
        self,
        *,
        message: str,
        conversation_id: str | None,
        customer_id: str | None,
        service_stage: str | None,
        entry_node: str | None = None,
    ) -> AgentState:
        entry = entry_node or "session_hydrate"
        run_id = str(uuid4())
        turn_id = str(uuid4())
        session_id = conversation_id or f"chat-{uuid4()}"
        workflow = WorkflowState(run_id=run_id, next_node=entry)
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
            next_node=entry,
        )
        state.sync_contract_state()
        return state

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
        state.sync_contract_state()
        if self._store is not None:
            await self._store.save(
                state,
                reason="workflow_completed",
            )
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
        # No virtual chat_turn exists in graph mode. Persist a transport-level
        # failure only when a non-node Graph error escapes without durable state.
        if state.status is WorkflowStatus.COMPLETED:
            return
        if state.error is None:
            state.status = WorkflowStatus.FAILED
            state.resume_from = None
            state.error = StateError(
                node="graph_transport",
                code=error_code,
                message="Agent Graph transport failure",
                retryable=True,
            )
        state.sync_contract_state()
        if self._store is not None:
            await self._store.save(
                state,
                reason=f"failed:{state.error.node}",
            )

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


def _require_session(state: AgentState) -> ChatSessionState:
    if state.session is None:
        raise ValueError("Chat State 缺少 session")
    return state.session


def _require_turn(state: AgentState) -> ChatTurnState:
    if state.turn is None:
        raise ValueError("Chat State 缺少 turn")
    return state.turn
