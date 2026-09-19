"""Guard 后的纯条件路由函数。"""

from __future__ import annotations

from app.agent.state import AgentState


def after_guard(state: AgentState) -> str:
    """仅根据已完成 Guard 的状态决策返回 terminal 或 continue。"""
    if state.context.get("guard_terminal") is True:
        return "terminal"
    if state.final_answer is not None:
        return "terminal"
    if state.session is not None and state.session.human.required:
        return "terminal"
    return "continue"


__all__ = ["after_guard"]
