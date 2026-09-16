"""节点执行协调器，统一处理状态合并、检查点与安全恢复。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.agent.checkpoint import CheckpointStore
from app.agent.state import (
    ActionStatus,
    AgentState,
    StatePatch,
    WorkflowStatus,
)


logger = logging.getLogger(__name__)
NodeHandler = Callable[[AgentState], Awaitable[StatePatch | None]]


class StateCoordinator:
    """在每个节点执行前后保存状态，并准备安全的断点恢复入口。"""

    def __init__(self, store: CheckpointStore) -> None:
        self._store = store

    async def create(self, state: AgentState) -> AgentState:
        """保存新流程的初始状态。"""
        return await self._store.save(state, reason="workflow_created")

    async def run_node(
        self,
        state: AgentState,
        node: str,
        handler: NodeHandler,
        *,
        retryable: bool = True,
    ) -> AgentState:
        """执行普通节点；节点只返回增量 Patch，不直接替换全量 State。"""
        state.begin_node(node)
        await self._store.save(state, reason=f"before:{node}")
        try:
            # 节点拿到深拷贝，只能通过 StatePatch 回传结果，避免无意中直接
            # 修改共享状态，导致异常时保存了半完成数据。
            patch = await handler(state.model_copy(deep=True))
            if patch is not None:
                state.apply_patch(patch)
            state.complete_node(node, next_node=state.next_node)
            return await self._store.save(state, reason=f"after:{node}")
        except Exception as exc:
            error_code = type(exc).__name__.upper()
            state.fail_node(node, code=error_code, retryable=retryable)
            logger.exception(
                "agent_node_failed",
                extra={
                    "event": "agent_node_failed",
                    "run_id": state.run_id,
                    "node": node,
                    "error_code": error_code,
                },
            )
            await self._store.save(state, reason=f"failed:{node}")
            raise

    async def checkpoint_action_prepared(
        self,
        state: AgentState,
        *,
        action_type: str,
        idempotency_key: str,
        reference_id: str | None = None,
    ) -> AgentState:
        """在发送、退款等副作用执行前保存 PREPARED 检查点。"""
        state.prepare_action(
            action_type,
            idempotency_key,
            reference_id=reference_id,
        )
        return await self._store.save(state, reason=f"action_prepared:{action_type}")

    async def checkpoint_action_started(self, state: AgentState) -> AgentState:
        """真正调用外部系统前标为 EXECUTING，并立即持久化。"""
        state.start_action()
        assert state.pending_action is not None
        return await self._store.save(
            state,
            reason=f"action_started:{state.pending_action.action_type}",
        )

    async def checkpoint_action_succeeded(
        self,
        state: AgentState,
        *,
        next_node: str | None = None,
    ) -> AgentState:
        """将外部动作成功和当前节点完成合并为一个检查点。"""
        state.finish_action()
        assert state.pending_action is not None
        action_type = state.pending_action.action_type
        if state.current_node is None:
            raise ValueError("外部动作成功时没有运行中的节点")
        state.complete_node(state.current_node, next_node=next_node)
        return await self._store.save(
            state,
            reason=f"action_succeeded:{action_type}",
        )

    async def load_for_resume(self, run_id: str) -> AgentState | None:
        """加载断点并按副作用状态决定自动重试或转人工。"""
        state = await self._store.load(run_id)
        if state is None:
            return None

        action = state.pending_action
        if action and action.status in {
            ActionStatus.EXECUTING,
            ActionStatus.UNCERTAIN,
        }:
            # 进程可能已经完成点击或提交，但来不及保存成功状态。自动重试会导致
            # 重复回复、重复退款等事故，因此只能等待人工核对外部系统。
            state.mark_action_uncertain()
            return await self._store.save(
                state,
                reason=f"resume_manual:{action.action_type}",
            )

        if action and action.status == ActionStatus.SUCCEEDED:
            if state.current_node is None:
                return state
            # 新实现会把 SUCCEEDED 与节点完成写入同一检查点。若读取到这种
            # 矛盾状态，说明来自旧版本或手工数据，保守转人工而不是重复副作用。
            state.status = WorkflowStatus.WAITING_MANUAL
            state.resume_from = None
            return await self._store.save(
                state,
                reason=f"resume_inconsistent:{action.action_type}",
            )

        if state.status == WorkflowStatus.RUNNING:
            state.status = WorkflowStatus.READY
            state.next_node = state.resume_from or state.current_node
            state.current_node = None
            return await self._store.save(state, reason="resume_interrupted_node")

        if (
            state.status == WorkflowStatus.FAILED
            and state.error is not None
            and state.error.retryable
        ):
            state.status = WorkflowStatus.READY
            state.next_node = state.resume_from
            return await self._store.save(state, reason="resume_retryable_failure")

        return state
