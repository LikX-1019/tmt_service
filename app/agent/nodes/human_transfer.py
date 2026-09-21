"""人工转移决策节点；不发送渠道消息或创建外部任务。"""

from __future__ import annotations

from app.agent.state import AgentState, HumanState, StatePatch
from app.agent.constants import RESPONSE_NODE


class HumanTransferNode:
    """规范化 HumanState 并保留上游已经生成的回答。"""

    async def __call__(self, state: AgentState) -> StatePatch:
        turn = state.turn
        session = state.session
        if turn is None or session is None:
            raise ValueError("HumanTransferNode 前必须完成 hydration")
        reply = turn.reply
        if reply is None or not state.final_answer:
            raise ValueError("HumanTransferNode 前必须已有最终回答")
        if reply.route != "human":
            raise ValueError("HumanTransferNode 只能处理 route=human")

        existing = session.human
        human = HumanState(
            required=True,
            status="requested"
            if existing.status in {"none", "requested"}
            else existing.status,
            reason_code=existing.reason_code or reply.reason_code or "human_required",
            reason=existing.reason,
            requested_at=existing.requested_at,
            assigned_agent_id=existing.assigned_agent_id,
            resume_allowed=existing.resume_allowed,
        )
        normalized_reply = reply.model_copy(update={"route": "human"})
        return StatePatch(
            human=human,
            reply=normalized_reply,
            route="human",
            final_answer=state.final_answer,
            next_node=RESPONSE_NODE,
        )


human_transfer_node = HumanTransferNode

__all__ = ["HumanTransferNode", "human_transfer_node"]
