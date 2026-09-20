"""Unified Chat 会话初始化节点。"""

from __future__ import annotations

from app.agent.dependencies import AgentCapabilities
from app.agent.state import (
    AgentState,
    ConversationProductReferenceState,
    ProductReference,
    ProductResolutionState,
    RecentTurnState,
    StatePatch,
)


def _binding_recent_products(binding: object) -> list[ConversationProductReferenceState]:
    """把 MySQL recent_products JSON 转换为有界 State 引用。"""
    result: list[ConversationProductReferenceState] = []
    seen: set[str] = set()
    for product in getattr(binding, "recent_products", None) or []:
        if not isinstance(product, dict):
            continue
        product_id = str(product.get("product_id") or "").strip()
        product_name = str(product.get("product_name") or "").strip()
        if not product_id or not product_name or product_id in seen:
            continue
        seen.add(product_id)
        result.append(
            ConversationProductReferenceState(
                product_id=product_id,
                product_name=product_name,
            )
        )
    return result[:5]


class SessionHydrateNode:
    """加载会话商品绑定和有界历史，只做 hydration，不做业务决策。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        """校验契约，加载绑定和最多 10 组历史，不执行 FAQ/Rule/LLM。"""
        if state.session is None or state.turn is None:
            raise ValueError("Agent Graph 输入必须挂载 Session 和 Turn")

        session = state.session
        turn = state.turn
        if session.thread_id != state.thread_id:
            raise ValueError("session.thread_id 与 AgentState.thread_id 不一致")
        if turn.session_id != session.session_id or turn.run_id != state.run_id:
            raise ValueError("ChatTurnState 与 Session/Run 契约不一致")
        if state.conversation_id and session.session_id != state.conversation_id:
            raise ValueError("session.session_id 与 conversation_id 不一致")

        binding = None
        if state.conversation_id and self._capabilities.conversations is not None:
            binding = await self._capabilities.conversations.get_binding(
                state.conversation_id
            )

        recent_turns: list[RecentTurnState] = []
        if state.conversation_id and self._capabilities.messages is not None:
            history = await self._capabilities.messages.list_recent_turns(
                state.conversation_id,
                limit=session.short_term_memory.max_recent_messages,
            )
            if history and history[-1].get("assistant") is None:
                last_customer = history[-1].get("customer")
                if last_customer == turn.original_query:
                    history = history[:-1]
            recent_turns = [
                RecentTurnState(
                    customer=item.get("customer"),
                    assistant=item.get("assistant"),
                )
                for item in history
            ]

        patch = StatePatch(
            context={"session_hydrated": True},
            recent_turns=recent_turns,
            next_node="faq_exact",
        )
        if binding is not None and binding.product_id:
            patch.current_product = ProductReference(
                product_id=binding.product_id,
                source="manual",
            )
            patch.product = ProductResolutionState(
                reference=turn.product.reference,
                recent_products=_binding_recent_products(binding),
                resolution_source=(
                    turn.product.resolution_source
                    if turn.product.reference is not None
                    else "conversation"
                ),
                # no: preserve below
                status=(
                    "pending"
                    if turn.product.reference is not None
                    else "not_required"
                ),
            )
        return patch


session_hydrate_node = SessionHydrateNode

__all__ = ["SessionHydrateNode", "session_hydrate_node"]
