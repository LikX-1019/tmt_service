"""Unified Chat 安全规则 Guard 节点。"""

from __future__ import annotations

from app.agent.dependencies import AgentCapabilities
from app.agent.state import AgentState, StatePatch
from app.agent.state_mappers import rule_result_to_patch
from app.rules.base import RuleContext


class GuardNode:
    """调用现有 RuleRegistry，并把结果转换为 StatePatch。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._rules = capabilities.rules

    async def __call__(self, state: AgentState) -> StatePatch:
        if state.session is None or state.turn is None:
            raise ValueError("GuardNode 前必须完成 Session/Turn hydration")

        message = state.turn.original_query or next(
            (
                item.content or ""
                for item in reversed(state.messages)
                if item.role == "customer"
            ),
            "",
        )
        current_product_id = (
            state.session.current_product.product_id
            if state.session.current_product is not None
            else None
        )
        result = self._rules.evaluate(
            message,
            RuleContext(
                session_id=state.session.session_id,
                customer_id=state.session.customer_id,
                current_product_id=current_product_id,
                service_stage=state.session.service_stage,
                channel="unified_chat",
            ),
        )
        return rule_result_to_patch(result)


guard_node = GuardNode

__all__ = ["GuardNode", "guard_node"]
