"""Guard 后的纯条件路由。"""

from __future__ import annotations

from app.agent.constants import (
    FAQ_EXACT_NODE,
    HUMAN_TRANSFER_NODE,
    RESPONSE_NODE,
    SOCIAL_NODE,
)
from app.agent.state import AgentState


def after_guard(state: AgentState) -> str:
    """Human 终态显式转移；其它 terminal 直接响应；Unified 继续先查 FAQ。"""
    if state.session is not None and state.session.human.required:
        return HUMAN_TRANSFER_NODE
    if state.turn is not None and state.turn.reply is not None:
        return RESPONSE_NODE
    if state.session is not None and state.session.channel == "pdd":
        return SOCIAL_NODE
    return FAQ_EXACT_NODE


__all__ = ["after_guard"]
