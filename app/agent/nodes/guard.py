"""安全 Guard 节点：复用 RuleRegistry，不复制规则实现。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent.state import AgentState, HumanState, StatePatch
from app.rules.base import RuleContext
from app.rules.registry import RuleRegistry, default_rule_registry


def _utcnow() -> datetime:
    """生成状态模型使用的 timezone-aware UTC 时间。"""
    return datetime.now(timezone.utc)


class GuardNode:
    """调用确定性规则能力，并把 RuleEvaluationResult 转换为 StatePatch。"""

    def __init__(self, rule_registry: RuleRegistry | None = None) -> None:
        """允许测试注入受控规则，生产 Skeleton 默认使用当前规则集。"""
        self._rules = rule_registry or default_rule_registry()

    async def __call__(self, state: AgentState) -> StatePatch:
        """执行规则，并把 terminal、human、risk 与固定回复映射进增量补丁。"""
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
                channel="agent_graph",
            ),
        )

        terminal = result.terminal_decision
        primary = terminal or (result.decisions[0] if result.decisions else None)
        patch = StatePatch(
            context={
                "guard_terminal": terminal is not None,
                "guard_rule_names": [
                    decision.rule_name
                    for decision in result.decisions
                    if decision.rule_name
                ],
            },
            intent=primary.reason_code or primary.rule_name if primary else None,
            next_node="response",
        )

        if terminal is not None:
            patch.route = terminal.route
            patch.intent = terminal.reason_code or terminal.rule_name
            patch.final_answer = terminal.fixed_reply

        if result.requires_human:
            reason_code = primary.reason_code if primary else "human_required"
            human = HumanState(
                required=True,
                status="requested",
                reason_code=reason_code,
                requested_at=_utcnow(),
            )
            patch.human = human
            patch.risk_level = "high"
            if reason_code:
                patch.risk_reasons = [reason_code]

        return patch


guard_node = GuardNode()

__all__ = ["GuardNode", "guard_node"]
