"""PDD Channel Adapter：BrowserMessage/Conversation 与 AgentState 的转换。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Sequence

from app.agent.constants import SESSION_HYDRATE_NODE
from app.agent.runtime import AgentRuntime
from app.agent.state import (
    AgentState,
    ChatSessionState,
    ChatTurnState,
    ProductReference,
    ConversationProductReferenceState,
    StateMessage,
    WorkflowState,
)
from app.core.config import Settings
from app.integrations.pdd.base import BrowserMessage
from app.qa.models import QAResult, RetrievalDocument
from app.schemas.chat import ChatProductView, ChatResponse
from app.services.auto_reply_policy import AutoReplyPolicy
from app.services.product_service import ProductAnswer


def pdd_messages_to_agent_state(
    *,
    conversation_id: str,
    shop_id: str,
    customer_id: str | None,
    batch: Sequence[BrowserMessage],
    goods_id: str | None = None,
    goods_name: str | None = None,
    platform_conversation_id: str | None = None,
    display_name: str | None = None,
    shop_name: str | None = None,
) -> AgentState:
    """把 PDD browser batch 映射为唯一 AgentState；不写第二套业务状态。"""
    query = "\n".join(message.content or "" for message in list(batch)[-5:]).strip()
    batch_key = sha256(
        "|".join(message.fingerprint for message in batch).encode()
    ).hexdigest()
    run_id = f"pdd-{conversation_id}-{batch_key[:16]}"
    turn_id = f"{run_id}:turn"
    session_id = conversation_id
    workflow = WorkflowState(run_id=run_id, next_node=SESSION_HYDRATE_NODE)
    session = ChatSessionState(
        session_id=session_id,
        thread_id=session_id,
        channel="pdd",
        shop_id=shop_id or None,
        customer_id=customer_id or None,
        service_stage="general",
        workflow=workflow,
        current_product=ProductReference(
            product_id=goods_id,
            source="platform_card",
        )
        if goods_id
        else None,
    )
    messages = [
        StateMessage(
            role="customer",
            content=message.content or "",
            session_id=session_id,
            turn_id=turn_id,
            platform_message_id=message.platform_message_id,
        )
        for message in batch[-5:]
    ]
    turn = ChatTurnState(
        turn_id=turn_id,
        session_id=session_id,
        run_id=run_id,
        input_message_id=messages[0].id if messages else turn_id,
        original_query=query,
        workflow=workflow,
    )
    if goods_id:
        turn.product.requires_product = True
        turn.product.reference = ProductReference(
            product_id=goods_id,
            source="platform_card",
        )
        turn.product.resolution_source = "conversation"
    if goods_id and goods_name:
        turn.product.recent_products = [
            ConversationProductReferenceState(
                product_id=goods_id,
                product_name=goods_name,
            )
        ]
    state = AgentState(
        run_id=run_id,
        thread_id=session_id,
        shop_id=shop_id or None,
        conversation_id=conversation_id,
        session=session,
        turn=turn,
        messages=messages,
        context={
            "pdd_platform_conversation_id": platform_conversation_id,
            "pdd_display_name": display_name,
            "pdd_shop_name": shop_name,
        },
        next_node=SESSION_HYDRATE_NODE,
    )
    state.sync_contract_state()
    return state


def pdd_context_to_agent_state(
    *,
    batch: Sequence[BrowserMessage],
    conversation: Mapping[str, Any],
    shop: Mapping[str, Any],
) -> AgentState:
    """把 Console conversation/shop 投影为 PDD AgentState，不做持久化。"""
    return pdd_messages_to_agent_state(
        conversation_id=str(conversation["id"]),
        shop_id=str(shop["id"]),
        customer_id=str(conversation.get("customer_id") or "") or None,
        batch=batch,
        goods_id=str(conversation.get("goods_id") or "") or None,
        goods_name=str(conversation.get("goods_name") or "") or None,
        platform_conversation_id=(
            str(conversation.get("platform_conversation_id") or "") or None
        ),
        display_name=str(conversation.get("display_name") or "") or None,
        shop_name=str(shop.get("name") or "") or None,
    )


def agent_state_to_chat_response(state: AgentState) -> ChatResponse:
    """复用通用 reply 字段，供测试与 parity 比较；不含发送决策。"""
    if state.turn is None or state.turn.reply is None:
        raise ValueError("PDD Agent State 缺少 AgentReplyState")
    reply = state.turn.reply
    return ChatResponse(
        conversation_id=state.conversation_id,
        answer=reply.answer,
        source=reply.source,
        route=reply.route,
        product=ChatProductView(id=reply.product_id, name=reply.product_name)
        if reply.product_id and reply.product_name
        else None,
        product_resolution=reply.product_resolution,
        rule_name=reply.rule_name,
        reason_code=reply.reason_code,
        confidence=reply.confidence,
        qa_hit=reply.qa_hit,
    )


@dataclass(frozen=True, slots=True)
class PDDAgentDecision:
    """Graph 输出的渠道无关决策快照，作为 AutoReplyPolicy 输入。"""

    route: str | None
    answer: str | None
    requires_human: bool
    source: str | None
    reason_code: str | None
    confidence: float | None
    product_id: str | None
    product_name: str | None
    qa_code: str | None
    top_score: float | None
    score_margin: float | None
    greeting_type: str | None
    recognition_source: str | None
    facts_supported: bool | None
    contains_sensitive_or_after_sales: bool | None
    needs_clarification: bool | None


def graph_state_to_pdd_decision(state: AgentState) -> PDDAgentDecision:
    """把 AgentState 收敛为 PDD policy 可消费的决策输入。"""
    reply = state.turn.reply if state.turn else None
    retrieval = state.turn.retrieval if state.turn else None
    top = retrieval.candidates[0] if retrieval and retrieval.candidates else None
    second = (
        retrieval.candidates[1] if retrieval and len(retrieval.candidates) > 1 else None
    )
    margin = (
        top.rerank_score - second.rerank_score
        if top
        and second
        and top.rerank_score is not None
        and second.rerank_score is not None
        else top.rerank_score
        if top
        else None
    )
    product_id = (
        reply.product_id
        if reply and reply.product_id
        else state.session.current_product.product_id
        if state.session and state.session.current_product
        else None
    )
    product_name = reply.product_name if reply and reply.product_name else None
    return PDDAgentDecision(
        route=("handoff" if state.session and state.session.human.required else reply.route)
        if reply
        else state.route,
        answer=None
        if state.session and state.session.human.required
        else reply.answer
        if reply
        else state.final_answer,
        requires_human=bool(state.session.human.required) if state.session else False,
        source=reply.source if reply else None,
        reason_code=reply.reason_code if reply else state.fallback_reason,
        confidence=reply.confidence if reply else None,
        product_id=product_id,
        product_name=product_name,
        qa_code=top.chunk_id if top else None,
        top_score=top.rerank_score if top else None,
        score_margin=margin,
        greeting_type=reply.greeting_type if reply else None,
        recognition_source=reply.recognition_source if reply else None,
        facts_supported=reply.facts_supported if reply else None,
        contains_sensitive_or_after_sales=(
            reply.contains_sensitive_or_after_sales if reply else None
        ),
        needs_clarification=reply.needs_clarification if reply else None,
    )


@dataclass(frozen=True, slots=True)
class PDDChannelDecision:
    """Graph 外的 PDD 发送策略结果；字段与 ReplyDecision 输入对齐。"""

    route: str
    action: str
    answer: str | None
    reason: str | None = None
    reason_code: str | None = None
    confidence: float | None = None
    product_id: str | None = None
    product_name: str | None = None
    qa_code: str | None = None
    top_score: float | None = None
    score_margin: float | None = None
    greeting_type: str | None = None
    recognition_source: str | None = None


def evaluate_pdd_channel_policy(
    state: AgentState,
    conversation: Mapping[str, Any],
    *,
    allow_auto: bool,
    auto_reply_policy: AutoReplyPolicy,
    product_answer_service: Any,
    settings: Settings,
    auto_reply_blockers: Sequence[str] = (),
    handoff_reply: str | None = None,
) -> PDDChannelDecision:
    """将 Graph 结果交给既有 PDD policy；不持久化、不排队、不发送。"""
    decision = graph_state_to_pdd_decision(state)
    if decision.requires_human:
        human = state.turn.human if state.turn else None
        return PDDChannelDecision(
            route="handoff",
            action="handoff",
            answer=(
                handoff_reply
                if decision.reason_code == "pdd_after_sale_or_dispute"
                else None
            ),
            reason=(human.reason if human and human.reason else state.final_answer),
            reason_code=decision.reason_code,
            product_id=decision.product_id,
            product_name=decision.product_name,
        )

    if decision.route == "greeting":
        source_label = "规则" if decision.recognition_source == "rule" else "模型"
        reason = (
            f"{source_label}识别为一般问候，已进入自动发送队列"
            if allow_auto
            else f"{source_label}识别为一般问候；{'；'.join(auto_reply_blockers)}，仅生成建议"
        )
        return PDDChannelDecision(
            route="greeting",
            action="auto_send" if allow_auto else "suggest",
            answer=decision.answer,
            reason=reason,
            confidence=decision.confidence,
            top_score=decision.confidence,
            greeting_type=decision.greeting_type,
            recognition_source=decision.recognition_source,
        )

    if decision.route in {"small_talk", "empathy"}:
        reason = (
            f"识别为{'社交' if decision.route == 'small_talk' else '情绪'}表达，已进入自动发送队列"
            if allow_auto
            else "识别为社交或情绪表达；未满足自动发送条件，仅生成建议"
        )
        return PDDChannelDecision(
            route=decision.route,
            action="auto_send" if allow_auto else "suggest",
            answer=decision.answer,
            reason=reason,
            confidence=decision.confidence,
            top_score=decision.confidence,
            recognition_source=decision.source,
        )

    if decision.route == "product":
        product_answer = ProductAnswer(
            answer=decision.answer or "商品回答需要人工确认",
            facts_supported=bool(decision.facts_supported),
            contains_sensitive_or_after_sales=bool(
                decision.contains_sensitive_or_after_sales
            ),
            needs_clarification=bool(decision.needs_clarification),
            confidence=decision.confidence or 0.0,
        )
        auto_send = product_answer_service.can_auto_send(
            product_answer,
            allow_auto=allow_auto,
            settings=settings,
        )
        return PDDChannelDecision(
            route="product",
            action="auto_send" if auto_send else "suggest",
            answer=decision.answer,
            reason=None if auto_send else "商品回答需要人工确认",
            confidence=decision.confidence,
            product_id=decision.product_id,
            product_name=decision.product_name,
            top_score=decision.confidence,
        )

    if decision.route == "error":
        return PDDChannelDecision(
            route="error",
            action="suggest",
            answer=None,
            reason="知识库或模型不可用",
            reason_code="KNOWLEDGE_UNAVAILABLE",
        )

    result = _qa_result_from_state(state)
    policy = auto_reply_policy.evaluate(
        state.turn.original_query if state.turn else "",
        result,
        dict(conversation),
        allow_auto=allow_auto,
    )
    return PDDChannelDecision(
        route=policy.route,
        action=policy.action,
        answer=policy.answer,
        reason=policy.reason,
        reason_code=policy.reason_code,
        confidence=decision.confidence,
        qa_code=policy.qa_code,
        top_score=policy.top_score,
        score_margin=policy.margin,
    )


def _qa_result_from_state(state: AgentState) -> QAResult:
    if state.turn is None or state.turn.reply is None:
        raise ValueError("PDD Agent State 缺少 QA reply")
    reply = state.turn.reply
    documents = [
        RetrievalDocument(
            chunk_id=item.chunk_id or item.document_id,
            title=item.title,
            content=item.content,
            source=item.source,
            metadata=item.metadata,
            dense_score=item.dense_score,
            bm25_score=item.bm25_score,
            fusion_score=item.fusion_score,
            rerank_score=item.rerank_score,
        )
        for item in state.turn.retrieval.candidates
    ]
    return QAResult(
        answer=reply.answer,
        route=reply.route or "fallback",
        confidence=reply.confidence,
        decision_factors={"faq_match_reason": reply.reason_code}
        if reply.reason_code
        else {},
        trace_documents=documents,
    )


async def invoke_pdd_agent(
    runtime: AgentRuntime,
    *,
    conversation_id: str,
    shop_id: str,
    customer_id: str | None,
    batch: Sequence[BrowserMessage],
    goods_id: str | None = None,
    goods_name: str | None = None,
) -> AgentState:
    """显式 PDD Graph 调用入口；G5A 不接入 ConsoleRuntime。"""
    state = pdd_messages_to_agent_state(
        conversation_id=conversation_id,
        shop_id=shop_id,
        customer_id=customer_id,
        batch=batch,
        goods_id=goods_id,
        goods_name=goods_name,
    )
    return await runtime.invoke(state)


__all__ = [
    "PDDAgentDecision",
    "PDDChannelDecision",
    "agent_state_to_chat_response",
    "evaluate_pdd_channel_policy",
    "graph_state_to_pdd_decision",
    "invoke_pdd_agent",
    "pdd_context_to_agent_state",
    "pdd_messages_to_agent_state",
]
