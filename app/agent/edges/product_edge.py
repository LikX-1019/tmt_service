"""商品解析与加载后的纯条件路由。"""

from __future__ import annotations

from app.agent.state import AgentState


def after_product_resolve(state: AgentState) -> str:
    """根据强类型 ProductResolutionState 决定终态、加载或 fallback。"""
    if state.turn is None:
        return "fallback"
    if state.turn.reply is not None:
        return "terminal_product_result"
    if state.turn.product.status == "pending":
        return "load_product"
    if state.turn.product.status == "not_required":
        return "fallback"
    return "terminal_product_result"


def after_product_load(state: AgentState) -> str:
    """加载成功进入商品回答，未找到等终态进入 response。"""
    if state.turn is not None and state.turn.product.status == "resolved":
        if state.context.get("product_answer_required") is not True:
            return "fallback"
        return "product_answer"
    return "response"


__all__ = ["after_product_load", "after_product_resolve"]
