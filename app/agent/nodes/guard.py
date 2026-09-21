"""Unified Chat 安全规则 Guard 节点。"""

from __future__ import annotations

from app.agent.dependencies import AgentCapabilities
from app.agent.state import AgentState, StatePatch
from app.agent.state_mappers import rule_result_to_patch


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
        from app.rules.base import RuleContext

        result = self._rules.evaluate(
            message,
            RuleContext(
                session_id=state.session.session_id,
                customer_id=state.session.customer_id,
                current_product_id=current_product_id,
                service_stage=state.session.service_stage,
                channel=state.session.channel,
                shop_id=state.session.shop_id,
            ),
        )
        patch = rule_result_to_patch(result)
        patch.product = state.turn.product.model_copy(
            update={
                "requires_product": result.requires_product,
                "product_rule_name": next(
                    (
                        decision.rule_name
                        for decision in result.decisions
                        if decision.requires_product and decision.rule_name
                    ),
                    None,
                ),
            }
        )
        return patch


guard_node = GuardNode

__all__ = ["GuardNode", "guard_node"]
