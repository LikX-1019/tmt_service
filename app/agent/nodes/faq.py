"""Unified Chat FAQ exact 节点。"""

from __future__ import annotations

from app.agent.dependencies import AgentCapabilities
from app.agent.state import AgentState, StatePatch
from app.agent.state_mappers import qa_result_to_patch


class FAQNode:
    """只调用 QAService.match_exact，保持 FAQ 精确命中语义。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        if self._capabilities.qa_provider is None:
            return StatePatch(context={"faq_hit": False}, next_node="guard")
        if state.session is None or state.turn is None:
            raise ValueError("FAQNode 前必须完成 hydration")

        session = state.session
        qa_service = await self._capabilities.qa_provider()
        result = qa_service.match_exact(
            state.turn.original_query,
            product_code=(
                session.current_product.product_id
                if session.current_product is not None
                else None
            ),
            product_name=self._bound_product_name(state),
            service_stage=session.service_stage,
        )
        if result is None:
            return StatePatch(context={"faq_hit": False}, next_node="guard")
        return qa_result_to_patch(result, next_node="response")

    @staticmethod
    def _bound_product_name(state: AgentState) -> str | None:
        """从当前 turn 的最近商品引用中找出绑定名称。"""
        product = state.turn.product if state.turn else None
        current_id = (
            state.session.current_product.product_id
            if state.session is not None and state.session.current_product is not None
            else None
        )
        for item in product.recent_products if product else []:
            if item.product_id == current_id:
                return item.product_name
        return None


faq_node = FAQNode

__all__ = ["FAQNode", "faq_node"]
