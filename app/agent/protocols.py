"""Agent Graph 的业务节点、路由与异常契约。

Graph 只依赖本模块定义的最小协议；具体能力仍然保存在独立 Service 中。
业务节点禁止直接修改共享状态，所有合法变化都必须通过 :class:`StatePatch` 返回。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.agent.state import AgentState, StatePatch


class AgentGraphError(RuntimeError):
    """Agent Graph 执行失败的统一错误基类。"""

    def __init__(
        self,
        message: str,
        *,
        failed_state: AgentState | None = None,
        node_name: str | None = None,
        original_exception: BaseException | None = None,
    ) -> None:
        """保留失败时最后已知状态，便于调用方记录和未来 G3 恢复。"""
        super().__init__(message)
        self.failed_state = failed_state
        self.node_name = node_name
        self.original_exception = original_exception


class AgentNodeExecutionError(AgentGraphError):
    """业务节点执行失败，并携带尚未持久化的安全失败状态。"""


class AgentGraphExecutionError(AgentGraphError):
    """LangGraph 编译或执行阶段失败，与单个业务节点失败区分。"""


@runtime_checkable
class AgentNode(Protocol):
    """所有客服业务节点的统一输入输出契约。"""

    async def __call__(self, state: AgentState) -> StatePatch:
        """读取状态快照并返回增量补丁；不得原地修改传入状态。"""
        raise NotImplementedError


@runtime_checkable
class ConditionalEdge(Protocol):
    """纯路由协议；实现只能读取已完成节点的决策结果。"""

    def __call__(self, state: AgentState) -> str:
        """返回有限且已在 Graph 中注册的 route key。"""
        raise NotImplementedError
