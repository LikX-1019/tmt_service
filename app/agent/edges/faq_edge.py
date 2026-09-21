"""FAQ 后的纯条件路由。"""

from __future__ import annotations

from app.agent.constants import PDD_INTENT_NODE, SOCIAL_NODE
from app.agent.state import AgentState


def after_faq(state: AgentState) -> str:
    """FAQ 命中进入 response；PDD miss 进入 intent，Unified miss 进入 social。"""
    if state.turn is not None and state.turn.reply is not None:
        return "response"
    return PDD_INTENT_NODE if state.session.channel == "pdd" else SOCIAL_NODE


__all__ = ["after_faq"]
