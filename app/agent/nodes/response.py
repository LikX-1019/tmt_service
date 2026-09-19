"""G1 响应汇聚节点；不生成真实客服业务答案。"""

from __future__ import annotations

from app.agent.state import AgentState, StatePatch


SKELETON_FINAL_ANSWER = "Agent Graph skeleton 已完成执行；业务回答将在后续阶段迁移。"


class ResponseNode:
    """保留 Guard 固定回复，并为 continue 路径提供明确的 Skeleton 终点。"""

    async def __call__(self, state: AgentState) -> StatePatch:
        """收敛最终答案；此阶段绝不调用 QA、商品、Fallback 或 LLM。"""
        final_answer = state.final_answer or state.draft_answer or SKELETON_FINAL_ANSWER
        return StatePatch(
            final_answer=final_answer,
            next_node=None,
        )


response_node = ResponseNode()

__all__ = ["ResponseNode", "response_node", "SKELETON_FINAL_ANSWER"]
