"""FAQ 后的纯条件路由。"""

from __future__ import annotations

from app.agent.state import AgentState


def after_faq(state: AgentState) -> str:
    """FAQ 命中进入 response，未命中保持 Legacy 顺序进入 guard。"""
    return "response" if state.turn is not None and state.turn.reply is not None else "guard"


__all__ = ["after_faq"]
