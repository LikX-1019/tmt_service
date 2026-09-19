"""Social 后的纯条件路由。"""

from __future__ import annotations

from app.agent.state import AgentState


def after_social(state: AgentState) -> str:
    """社交命中进入 response，否则进入商品解析。"""
    return (
        "response"
        if state.turn is not None and state.turn.reply is not None
        else "product_resolve"
    )


__all__ = ["after_social"]
