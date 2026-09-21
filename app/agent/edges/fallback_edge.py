"""Fallback 后的纯条件路由。"""

from __future__ import annotations

from app.agent.constants import HUMAN_TRANSFER_NODE, RESPONSE_NODE
from app.agent.state import AgentState


def after_fallback(state: AgentState) -> str:
    """根据已生成的 Reply 和 HumanState 决定是否显式人工转移。"""
    human_required = state.session is not None and state.session.human.required
    reply_human = (
        state.turn is not None
        and state.turn.reply is not None
        and state.turn.reply.route == "human"
    )
    return HUMAN_TRANSFER_NODE if human_required or reply_human else RESPONSE_NODE


__all__ = ["after_fallback"]
