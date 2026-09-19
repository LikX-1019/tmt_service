"""构建可执行的 Agent StateGraph。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.edges.guard_edge import after_guard
from app.agent.nodes.guard import GuardNode
from app.agent.nodes.response import ResponseNode
from app.agent.nodes.session import SessionHydrateNode
from app.agent.protocols import (
    AgentGraphError,
    AgentGraphExecutionError,
    AgentNode,
    AgentNodeExecutionError,
)
from app.agent.state import AgentState, StatePatch
from app.rules.registry import RuleRegistry


def _safe_error_code(exc: BaseException) -> str:
    """从异常类型生成可观测且不含动态细节的错误码。"""
    return type(exc).__name__.upper()


class GraphNodeAdapter:
    """LangGraph transport adapter：把 StatePatch 业务契约接入 StateGraph。

    LangGraph 节点需要返回状态 update；本适配器负责 deepcopy、生命周期、
    唯一合法的 ``AgentState.apply_patch`` 合并，以及输出整份状态快照。
    它不引入第二套业务字段，也不执行持久化检查点。
    """

    def __init__(self, node_name: str, node: AgentNode) -> None:
        """绑定 Graph 节点名与业务 Node。"""
        self.node_name = node_name
        self._node = node

    async def __call__(self, raw_state: Any) -> dict[str, Any]:
        """规范化 LangGraph 状态、隔离输入、应用补丁并返回可传输快照。"""
        try:
            state = AgentState.model_validate(raw_state)
        except Exception as exc:
            raise AgentGraphExecutionError(
                "LangGraph 传入状态不是合法 AgentState"
            ) from exc

        working = state.model_copy(deep=True)
        try:
            working.begin_node(self.node_name)
            patch = await self._node(working)
            if not isinstance(patch, StatePatch):
                raise TypeError("Agent Node 必须返回 StatePatch")
            working.apply_patch(patch)
            working.complete_node(self.node_name, next_node=patch.next_node)
            return working.model_dump()
        except Exception as exc:
            code = _safe_error_code(exc)
            try:
                if working.current_node == self.node_name:
                    working.fail_node(
                        self.node_name,
                        code=code,
                        retryable=True,
                        message=str(exc) or "Agent Node 执行失败",
                    )
            except Exception:
                # 状态生命周期失败时仍保留原始异常和最后已知副本。
                pass
            raise AgentNodeExecutionError(
                f"Agent Node {self.node_name} 执行失败",
                failed_state=working,
                node_name=self.node_name,
                original_exception=exc,
            ) from exc


def build_agent_graph(
    rule_registry: RuleRegistry | None = None,
) -> CompiledStateGraph:
    """构建 G1 最小拓扑：START → hydrate → guard → response → END。"""
    builder: StateGraph = StateGraph(AgentState)
    builder.add_node(
        "session_hydrate",
        GraphNodeAdapter("session_hydrate", SessionHydrateNode()),
    )
    builder.add_node("guard", GraphNodeAdapter("guard", GuardNode(rule_registry)))
    builder.add_node("response", GraphNodeAdapter("response", ResponseNode()))
    builder.add_edge(START, "session_hydrate")
    builder.add_edge("session_hydrate", "guard")
    builder.add_conditional_edges(
        "guard",
        after_guard,
        {"terminal": "response", "continue": "response"},
    )
    builder.add_edge("response", END)
    try:
        # G1 显式不配置 LangGraph checkpointer；持久化契约仍留给 G3。
        return builder.compile()
    except Exception as exc:
        raise AgentGraphExecutionError("Agent StateGraph 编译失败") from exc


__all__ = [
    "AgentGraphError",
    "AgentGraphExecutionError",
    "AgentNodeExecutionError",
    "GraphNodeAdapter",
    "build_agent_graph",
]
