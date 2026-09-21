"""Unified Chat facade backed exclusively by the shared Agent Graph."""

from __future__ import annotations

import logging
from uuid import uuid4

from app.agent.chat_runtime import ChatStateRuntime
from app.agent.protocols import AgentGraphError
from app.agent.runtime import AgentRuntime
from app.agent.state import ProductReference
from app.core.exceptions import AppException
from app.repositories.chat_message_repository import ChatConversationMessageRepository
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_response_mapper import agent_state_to_chat_response


logger = logging.getLogger(__name__)


def _log_chat_response(
    message: str,
    response: ChatResponse,
    *,
    history_turn_count: int,
    history_loaded: bool,
    run_id: str,
    completed_node_count: int,
) -> None:
    logger.info(
        "chat_response_completed",
        extra={
            "event": "chat_response_completed",
            "message_length": len(message),
            "history_turn_count": history_turn_count,
            "history_loaded": history_loaded,
            "agent_runtime": "graph",
            "run_id": run_id,
            "completed_node_count": completed_node_count,
            "route": response.route,
            "intent": response.route,
            "qa_exact_hit": response.route == "faq",
            "product_context_used": response.product is not None,
            "social_intent": (
                response.route
                if response.route in {"small_talk", "empathy"}
                else None
            ),
            "product_resolution": response.product_resolution,
            "resolved_product": response.product.id if response.product else None,
            "answer_source": response.source,
            "fallback_reason": (
                response.reason_code
                if response.route in {"fallback", "llm_fallback"}
                else None
            ),
        },
    )


class ChatService:
    """Adapt Unified Chat transport to the shared AgentRuntime."""

    def __init__(
        self,
        *,
        agent_runtime: AgentRuntime,
        state_runtime: ChatStateRuntime | None = None,
        message_repository: ChatConversationMessageRepository | None = None,
    ) -> None:
        self._agent_runtime = agent_runtime
        self._state_runtime = state_runtime or ChatStateRuntime()
        self._messages = message_repository

    async def chat(self, request: ChatRequest) -> ChatResponse:
        if request.conversation_id is None:
            request = request.model_copy(
                update={"conversation_id": f"chat-{uuid4()}"}
            )
        conversation_id = request.conversation_id
        assert conversation_id is not None
        state = self._state_runtime.create_state(
            message=request.message,
            conversation_id=conversation_id,
            customer_id=request.customer_id,
            service_stage=request.service_stage,
        )
        try:
            await self._state_runtime.start(state)
            if self._messages is not None:
                await self._messages.append_customer_message(
                    conversation_id, request.message
                )
            if request.product_id:
                state.turn.product.reference = ProductReference(
                    product_id=request.product_id,
                    source="customer_selected",
                )
                state.turn.product.resolution_source = "request"
            state = await self._agent_runtime.invoke(state)
            response = agent_state_to_chat_response(state)
            history = (
                [
                    {"customer": item.customer, "assistant": item.assistant}
                    for item in state.session.short_term_memory.recent_turns
                ]
                if state.session is not None
                else []
            )
            await self._state_runtime.complete(state, response)
            if self._messages is not None:
                await self._messages.append_assistant_message(
                    conversation_id, response.answer
                )
            _log_chat_response(
                request.message,
                response,
                history_turn_count=len(history),
                history_loaded=self._messages is not None,
                run_id=state.run_id,
                completed_node_count=len(state.completed_nodes),
            )
            return response
        except Exception as exc:
            if isinstance(exc, AgentGraphError):
                if exc.failed_state is not None:
                    state = exc.failed_state
                original = exc.original_exception
                if isinstance(original, AppException):
                    raise original from exc
                raise
            try:
                await self._state_runtime.fail(state, exc)
            except Exception:
                logger.exception(
                    "chat_state_failure_checkpoint_failed",
                    extra={
                        "event": "chat_state_failure_checkpoint_failed",
                        "run_id": state.run_id,
                        "failed_node": state.error.node if state.error else None,
                    },
                )
            raise
