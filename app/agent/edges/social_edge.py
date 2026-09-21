"""Social 后的纯条件路由。"""

from __future__ import annotations

from app.agent.state import AgentState


def after_social(state: AgentState) -> str:
    """社交命中进入 response；PDD 继续 FAQ，Unified 继续商品解析。"""
    if state.context.get("pdd_greeting_hit") is True:
        return "response"
    if state.turn is not None and state.turn.reply is not None:
        return "response"
    return "faq_exact" if state.session.channel == "pdd" else "product_resolve"


__all__ = ["after_social"]
