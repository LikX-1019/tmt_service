"""会话初始化 Skeleton 节点。"""

from __future__ import annotations

from app.agent.state import AgentState, StatePatch


class SessionHydrateNode:
    """校验当前内存 State 的 Session/Turn 契约，不访问任何数据库。"""

    async def __call__(self, state: AgentState) -> StatePatch:
        """确认 Graph 输入已经挂载一致的业务会话与单轮状态。"""
        if state.session is None or state.turn is None:
            raise ValueError("Agent Graph 输入必须挂载 ChatSessionState 和 ChatTurnState")

        session = state.session
        turn = state.turn
        if session.thread_id != state.thread_id:
            raise ValueError("session.thread_id 与 AgentState.thread_id 不一致")
        if turn.session_id != session.session_id or turn.run_id != state.run_id:
            raise ValueError("ChatTurnState 与 Session/Run 契约不一致")
        if (
            state.conversation_id is not None
            and session.session_id != state.conversation_id
        ):
            raise ValueError("session.session_id 与 conversation_id 不一致")

        return StatePatch(
            context={"session_hydrated": True},
            next_node="guard",
        )


session_hydrate_node = SessionHydrateNode()

__all__ = ["SessionHydrateNode", "session_hydrate_node"]
