"""Agent Graph 响应汇聚节点。"""

from __future__ import annotations

from app.agent.state import AgentState, StatePatch


class ResponseNode:
    """校验前序 Node 已形成完整 AgentReplyState，并收敛最终答案。"""

    async def __call__(self, state: AgentState) -> StatePatch:
        """没有 reply 或 answer 属于 Graph invariant violation。"""
        if state.turn is None or state.turn.reply is None or not state.final_answer:
            raise ValueError("ResponseNode 前必须生成完整 AgentReplyState")
        return StatePatch(
            reply=state.turn.reply,
            final_answer=state.final_answer,
            next_node=None,
        )


response_node = ResponseNode

__all__ = ["ResponseNode", "response_node"]
