"""Guard 后的纯条件路由。"""

from __future__ import annotations

from app.agent.state import AgentState


def after_guard(state: AgentState) -> str:
    """terminal rule 或 human decision 进入 response，否则进入 social。"""
    if state.turn is not None and state.turn.reply is not None:
        return "terminal"
    if state.session is not None and state.session.human.required:
        return "terminal"
    return "continue"


__all__ = ["after_guard"]
