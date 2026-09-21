"""商品解析与加载后的纯条件路由。"""

from __future__ import annotations

from app.agent.state import AgentState

from app.agent.constants import (
    FALLBACK_NODE,
    HUMAN_TRANSFER_NODE,
    PRODUCT_ANSWER_NODE,
    PRODUCT_LOAD_NODE,
    RAG_CONTEXT_NODE,
    RESPONSE_NODE,
)


def after_product_resolve(state: AgentState) -> str:
    """根据强类型 ProductResolutionState 决定终态、加载或 fallback。"""
    if state.turn is None:
        return FALLBACK_NODE
    if state.turn.human.required:
        return HUMAN_TRANSFER_NODE
    if state.turn.reply is not None:
        return RESPONSE_NODE
    if state.turn.product.status == "pending":
        return PRODUCT_LOAD_NODE
    if state.turn.product.status == "not_required":
        return RAG_CONTEXT_NODE
    return RESPONSE_NODE


def after_product_load(state: AgentState) -> str:
    """加载成功进入商品回答，未找到等终态进入 response。"""
    if state.turn is None:
        return RESPONSE_NODE
    if state.turn.human.required:
        return HUMAN_TRANSFER_NODE
    if state.turn.product.status != "resolved":
        return RESPONSE_NODE
    if state.turn.product.answer_required:
        return PRODUCT_ANSWER_NODE
    if state.turn.product.requires_product:
        return RAG_CONTEXT_NODE
    return FALLBACK_NODE


__all__ = ["after_product_load", "after_product_resolve"]
