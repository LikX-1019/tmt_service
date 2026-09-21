"""Unified Chat 社交/情绪表达节点。"""

from __future__ import annotations

from app.agent.dependencies import AgentCapabilities
from app.agent.state import AgentReplyState, AgentState, StatePatch


class SocialNode:
    """仅在 Graph social 分支条件内调用 SocialRouter.classify。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._social_router = capabilities.social_router

    async def __call__(self, state: AgentState) -> StatePatch:
        session = state.session
        turn = state.turn
        if session is None or turn is None:
            raise ValueError("SocialNode 前必须完成 hydration")

        has_binding = session.current_product is not None
        explicit_product = turn.product.reference is not None
        requires_product = turn.product.requires_product
        if session.channel != "pdd" and (
            has_binding or explicit_product or requires_product
        ):
            return StatePatch(context={"social_hit": False}, next_node="product_resolve")
        decision = await self._social_router.classify(turn.original_query)
        if decision.intent == "other" or decision.response is None:
            return StatePatch(
                context={"social_hit": False},
                next_node="faq_exact" if session.channel == "pdd" else "product_resolve",
            )

        return StatePatch(
            context={"social_hit": True},
            route=decision.intent,
            final_answer=decision.response,
            reply=AgentReplyState(
                source="rule",
                route=decision.intent,
                answer=decision.response,
                product_resolution=(
                    "conversation" if has_binding or explicit_product else "none"
                ),
                rule_name="SocialRouter",
                reason_code=decision.reason_code,
                confidence=decision.confidence,
            ),
            next_node="response",
        )


social_node = SocialNode

__all__ = ["SocialNode", "social_node"]
