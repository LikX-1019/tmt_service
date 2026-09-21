"""FAQ 后的纯条件路由。"""

from __future__ import annotations

from app.agent.state import AgentState


def after_faq(state: AgentState) -> str:
    """FAQ 命中进入 response；PDD miss 进入 intent，Unified miss 进入 guard。"""
    if state.turn is not None and state.turn.reply is not None:
        return "response"
    return "pdd_intent" if state.session.channel == "pdd" else "guard"


__all__ = ["after_faq"]
