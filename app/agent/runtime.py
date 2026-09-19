"""AgentRuntime：显式执行 LangGraph 的稳定入口。"""

from __future__ import annotations

from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.agent.graph import build_agent_graph
from app.agent.protocols import (
    AgentGraphError,
    AgentGraphExecutionError,
    AgentNodeExecutionError,
)
from app.agent.state import AgentState


class AgentRuntime:
    """持有编译后的 Graph，并以异步方式输入/输出 AgentState。"""

    def __init__(self, graph: CompiledStateGraph | None = None) -> None:
        """允许测试注入受控 Graph；生产 Skeleton 默认构建当前最小拓扑。"""
        self._graph = graph or build_agent_graph()

    async def invoke(self, state: AgentState) -> AgentState:
        """异步执行 Graph，并严格重新校验输出为 AgentState。"""
        if not isinstance(state, AgentState):
            raise TypeError("AgentRuntime.invoke 只接受 AgentState")

        original = state.model_copy(deep=True)
        try:
            result: Any = await self._graph.ainvoke(state)
            # LangGraph 的 Pydantic state 输出可能是 dict；不能假设类型安全。
            return AgentState.model_validate(result)
        except AgentNodeExecutionError:
            raise
        except Exception as exc:
            failed_state = original.model_copy(deep=True)
            if not isinstance(exc, AgentGraphError):
                raise AgentGraphExecutionError(
                    "Agent Graph 执行失败",
                    failed_state=failed_state,
                    original_exception=exc,
                ) from exc
            if exc.failed_state is None:
                exc.failed_state = failed_state
            raise


__all__ = ["AgentRuntime"]
