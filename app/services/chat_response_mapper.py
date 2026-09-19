"""Agent State 到 Unified Chat API response 的薄映射层。"""

from __future__ import annotations

from app.agent.state import AgentState
from app.schemas.chat import (
    ChatProductCandidateView,
    ChatProductView,
    ChatResponse,
    ChatSourceView,
)


def agent_state_to_chat_response(state: AgentState) -> ChatResponse:
    """把完整 Agent Reply State 映射为 API contract；不执行业务决策。"""
    if state.turn is None or state.turn.reply is None:
        raise ValueError("Agent State 缺少可映射的 AgentReplyState")
    reply = state.turn.reply

    product = (
        ChatProductView(id=reply.product_id, name=reply.product_name)
        if reply.product_id is not None and reply.product_name is not None
        else None
    )
    if product is None and state.turn.product.context is not None:
        context = state.turn.product.context
        product = ChatProductView(id=context.product_id, name=context.name)

    has_binding = state.session is not None and state.session.current_product is not None
    has_request_product = (
        state.turn.product.reference is not None
        and state.turn.product.reference.source == "customer_selected"
    )
    product_resolution = reply.product_resolution
    if product_resolution == "none" and reply.source in {"rule", "qa", "llm_fallback"}:
        product_resolution = (
            "request"
            if has_request_product
            else "conversation"
            if has_binding
            else "none"
        )

    return ChatResponse(
        conversation_id=state.conversation_id,
        answer=reply.answer,
        source=reply.source,
        route=reply.route,
        product=product,
        products=[
            ChatProductCandidateView(
                id=item.id,
                name=item.name,
                summary=item.summary,
                internal_code=item.internal_code,
                specifications=item.specifications,
            )
            for item in reply.products
        ],
        product_resolution=product_resolution,
        rule_name=reply.rule_name,
        reason_code=reply.reason_code,
        confidence=reply.confidence,
        qa_hit=reply.qa_hit,
        sources=[
            ChatSourceView(
                chunk_id=item.chunk_id,
                title=item.title,
                source=item.source,
                score=item.score,
            )
            for item in reply.sources
        ],
    )


__all__ = ["agent_state_to_chat_response"]
