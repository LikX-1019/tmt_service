"""PDD 共用业务节点；只做 Agent 决策，不处理渠道发送策略。"""

from __future__ import annotations

from app.agent.constants import (
    HUMAN_TRANSFER_NODE,
    PDD_GREETING_NODE,
    PRODUCT_RESOLVE_NODE,
    RESPONSE_NODE,
    SOCIAL_NODE,
)
from app.agent.dependencies import AgentCapabilities
from app.agent.state import (
    AgentReplyState,
    AgentState,
    HumanState,
    RetrievalState,
    StatePatch,
)
from app.agent.service import CustomerContext
from app.agent.state_mappers import qa_result_to_patch


class PDDHandoffNode:
    """执行 PDD deterministic hard-handoff 判断。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        router = self._capabilities.pdd_router
        query = state.turn.original_query if state.turn else ""
        hard_handoff = router is not None and router.requires_handoff(query)
        if not hard_handoff:
            return StatePatch(
                context={"pdd_handoff": False},
                next_node=PDD_GREETING_NODE,
            )
        return StatePatch(
            context={"pdd_handoff": True},
            route="human",
            risk_level="high",
            risk_reasons=["pdd_after_sale_or_dispute"],
            final_answer="售后或争议问题已转人工处理",
            human=HumanState(
                required=True,
                status="requested",
                reason_code="pdd_after_sale_or_dispute",
            ),
            reply=AgentReplyState(
                source="rule",
                route="human",
                answer="售后或争议问题已转人工处理",
                reason_code="pdd_after_sale_or_dispute",
                confidence=1.0,
                product_id=state.session.current_product.product_id
                if state.session and state.session.current_product
                else None,
                product_name=self._product_name(state),
            ),
            next_node=HUMAN_TRANSFER_NODE,
        )

    @staticmethod
    def _product_name(state: AgentState) -> str | None:
        current_id = (
            state.session.current_product.product_id
            if state.session and state.session.current_product
            else None
        )
        for item in state.turn.product.recent_products if state.turn else []:
            if item.product_id == current_id:
                return item.product_name
        return None


class PDDGreetingNode:
    """复用 CustomerServiceAgent，只识别并生成 greeting 决策。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        session, turn = state.session, state.turn
        if session is None or turn is None:
            raise ValueError("PDDGreetingNode 前必须完成 hydration")
        agent = self._capabilities.greeting_agent
        loader = self._capabilities.greeting_config_loader
        if agent is None or loader is None:
            return StatePatch(context={"pdd_greeting_hit": False}, next_node=SOCIAL_NODE)

        try:
            config = (
                await loader(session.shop_id) if session.shop_id else await loader(None)
            )
        except Exception:
            config = None
        customer = CustomerContext(
            customer_id=session.customer_id,
            platform_customer_id=(
                str(state.context.get("pdd_platform_conversation_id") or "") or None
            ),
            display_name=str(state.context.get("pdd_display_name") or "") or None,
            shop_id=session.shop_id,
            shop_name=str(state.context.get("pdd_shop_name") or "") or None,
            goods_id=(
                session.current_product.product_id
                if session.current_product
                else None
            ),
            goods_name=self._product_name(state),
        )
        try:
            reply = await agent.run(
                turn.original_query, customer, greeting_config=config
            )
        except Exception:
            return StatePatch(
                context={"pdd_greeting_hit": False}, next_node=SOCIAL_NODE
            )
        if reply is None or reply.intent != "daily_greeting" or not reply.answer:
            return StatePatch(
                context={"pdd_greeting_hit": False}, next_node=SOCIAL_NODE
            )
        return StatePatch(
            context={
                "pdd_greeting_hit": True,
                "pdd_greeting_blockers": [],
            },
            route="greeting",
            final_answer=reply.answer,
            reply=AgentReplyState(
                source="rule",
                route="greeting",
                answer=reply.answer,
                greeting_type=reply.greeting_type,
                recognition_source=reply.recognition_source,
                confidence=reply.confidence,
            ),
            next_node=SOCIAL_NODE,
        )

    @staticmethod
    def _product_name(state: AgentState) -> str | None:
        current_id = (
            state.session.current_product.product_id
            if state.session.current_product
            else None
        )
        for item in state.turn.product.recent_products:
            if item.product_id == current_id:
                return item.product_name
        return None


class PDDIntentNode:
    """复用 PDD Router，将 FAQ miss 分类为 product 或 knowledge。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        router = self._capabilities.pdd_router
        if router is None:
            classification = type("C", (), {"route": "rag"})()
        else:
            classification = await router.classify(
                state.turn.original_query,
                has_product_context=bool(state.session.current_product),
            )
        requires_product = classification.route == "product"
        return StatePatch(
            product=state.turn.product.model_copy(
                update={"requires_product": requires_product}
            ),
            context={"pdd_product_route": requires_product},
            next_node=PRODUCT_RESOLVE_NODE
            if requires_product
            else "pdd_rag",
        )


class PDDRAGNode:
    """执行 PDD RAG/knowledge decision；不评估渠道发送策略。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        if self._capabilities.qa_provider is None:
            return self._fallback("knowledge_inactive")
        qa_service = await self._capabilities.qa_provider()
        if qa_service is None:
            return self._fallback("knowledge_inactive")
        try:
            result = await qa_service.answer_rag(
                state.turn.original_query,
                product_code=(
                    state.session.current_product.product_id
                    if state.session.current_product
                    else None
                ),
                product_name=self._product_name(state),
            )
        except Exception:
            return self._unavailable()
        patch = qa_result_to_patch(result, next_node=RESPONSE_NODE)
        if result.route == "fallback":
            patch.fallback_reason = "knowledge_evidence_insufficient"
        return patch

    def _fallback(self, reason_code: str) -> StatePatch:
        """PDD knowledge fallback answer; policy remains outside graph."""
        return StatePatch(
            retrieval=RetrievalState(status="unavailable"),
            route="fallback",
            fallback_reason=reason_code,
            final_answer="目前知识库中暂无相关信息，请联系人工客服获取帮助。",
            reply=AgentReplyState(
                source="qa",
                route="fallback",
                answer="目前知识库中暂无相关信息，请联系人工客服获取帮助。",
                qa_hit=False,
                reason_code=reason_code,
            ),
            next_node=RESPONSE_NODE,
        )

    @staticmethod
    def _unavailable() -> StatePatch:
        return StatePatch(
            retrieval=RetrievalState(status="unavailable"),
            route="error",
            final_answer="知识库或模型不可用",
            reply=AgentReplyState(
                source="qa",
                route="error",
                answer="知识库或模型不可用",
                qa_hit=False,
                reason_code="KNOWLEDGE_UNAVAILABLE",
            ),
            next_node=RESPONSE_NODE,
        )

    @staticmethod
    def _product_name(state: AgentState) -> str | None:
        current_id = (
            state.session.current_product.product_id
            if state.session.current_product
            else None
        )
        if state.turn is None:
            return None
        for item in state.turn.product.recent_products:
            if item.product_id == current_id:
                return item.product_name
        return None


pdd_handoff_node = PDDHandoffNode
pdd_greeting_node = PDDGreetingNode
pdd_intent_node = PDDIntentNode
pdd_rag_node = PDDRAGNode

__all__ = [
    "PDDGreetingNode",
    "PDDHandoffNode",
    "PDDIntentNode",
    "PDDRAGNode",
    "pdd_greeting_node",
    "pdd_handoff_node",
    "pdd_intent_node",
    "pdd_rag_node",
]
