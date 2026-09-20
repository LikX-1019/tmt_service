"""AgentRuntime：显式执行与恢复 Agent Graph 的稳定入口。"""

from __future__ import annotations

from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.agent.coordinator import StateCoordinator
from app.agent.graph import build_agent_graph
from app.agent.protocols import (
    AgentGraphError,
    AgentGraphExecutionError,
    AgentNodeExecutionError,
)
from app.agent.state import AgentState, WorkflowStatus


class AgentRuntime:
    """持有编译后的 Graph，并以异步方式输入/输出 AgentState。"""

    def __init__(
        self,
        graph: CompiledStateGraph | None = None,
        coordinator: StateCoordinator | None = None,
    ) -> None:
        """生产传入 durable coordinator；测试可省略以保留 in-memory mode。"""
        self._graph = graph or build_agent_graph()
        self._coordinator = coordinator

    async def invoke(self, state: AgentState) -> AgentState:
        """异步执行新 Graph run，并在 durable 模式下保存 workflow_created。"""
        if not isinstance(state, AgentState):
            raise TypeError("AgentRuntime.invoke 只接受 AgentState")

        original = state.model_copy(deep=True)
        try:
            if self._coordinator is not None:
                if (
                    state.status != WorkflowStatus.READY
                    or state.current_node is not None
                ):
                    raise AgentGraphExecutionError(
                        "AgentRuntime.invoke 只接受 READY 的新 Graph run"
                    )
                state = await self._coordinator.create(state)
            result: Any = await self._graph.ainvoke(state)
            return AgentState.model_validate(result)
        except AgentNodeExecutionError:
            raise
        except Exception as exc:
            failure_source = state if self._coordinator is not None else original
            failed_state = failure_source.model_copy(deep=True)
            if not isinstance(exc, AgentGraphError):
                raise AgentGraphExecutionError(
                    "Agent Graph 执行失败",
                    failed_state=failed_state,
                    original_exception=exc,
                ) from exc
            if exc.failed_state is None:
                exc.failed_state = failed_state
            raise

    async def resume(self, run_id: str) -> AgentState | None:
        """显式恢复指定 run；绝不扫描或自动恢复其它 checkpoint。"""
        if self._coordinator is None:
            raise AgentGraphExecutionError("当前 Runtime 未配置 durable coordinator")
        state = await self._coordinator.load_for_resume(run_id)
        if state is None:
            return None
        if state.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.WAITING_MANUAL,
        }:
            return state
        if state.status != WorkflowStatus.READY or state.current_node is not None:
            raise AgentGraphExecutionError(
                "checkpoint 状态不容许 Graph resume",
                failed_state=state,
            )
        try:
            result: Any = await self._graph.ainvoke(state)
            return AgentState.model_validate(result)
        except AgentNodeExecutionError:
            raise
        except Exception as exc:
            if not isinstance(exc, AgentGraphError):
                raise AgentGraphExecutionError(
                    "Agent Graph resume 执行失败",
                    failed_state=state,
                    original_exception=exc,
                ) from exc
            if exc.failed_state is None:
                exc.failed_state = state
            raise


__all__ = ["AgentRuntime"]
