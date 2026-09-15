"""客服 Agent 的统一状态契约与节点间增量更新模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


STATE_SCHEMA_VERSION = 1


def utcnow() -> datetime:
    """返回带 UTC 时区的时间，保证检查点可跨时区恢复。"""
    return datetime.now(timezone.utc)


class WorkflowStatus(StrEnum):
    """一次 Agent 流程的生命周期状态。"""

    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    WAITING_MANUAL = "waiting_manual"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(StrEnum):
    """单个节点的执行状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionStatus(StrEnum):
    """外部副作用的提交状态，用于避免恢复时重复执行。"""

    PREPARED = "prepared"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class StateMessage(BaseModel):
    """在节点之间传递的标准消息，避免依赖具体模型 SDK。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: Literal["system", "customer", "assistant", "tool"]
    kind: str = "text"
    content: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalEvidence(BaseModel):
    """RAG 节点写入的精简证据，供决策和回复节点继续使用。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    content: str
    source: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StateError(BaseModel):
    """可持久化的安全错误摘要，不保存堆栈和敏感输入。"""

    model_config = ConfigDict(extra="forbid")

    node: str
    code: str
    message: str = "节点执行失败"
    retryable: bool = False
    occurred_at: datetime = Field(default_factory=utcnow)


class NodeTrace(BaseModel):
    """节点执行轨迹，用于恢复定位和运行审计。"""

    model_config = ConfigDict(extra="forbid")

    node: str
    attempt: int
    status: NodeStatus = NodeStatus.RUNNING
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    error_code: str | None = None


class PendingAction(BaseModel):
    """需要外部提交的动作及其幂等信息。"""

    model_config = ConfigDict(extra="forbid")

    action_type: str
    idempotency_key: str
    status: ActionStatus = ActionStatus.PREPARED
    reference_id: str | None = None
    prepared_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class StatePatch(BaseModel):
    """节点返回的增量结果；集合字段按追加或合并语义写入 State。"""

    model_config = ConfigDict(extra="forbid")

    messages: list[StateMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    slots: dict[str, Any] = Field(default_factory=dict)
    tool_results: dict[str, Any] = Field(default_factory=dict)
    evidence: list[RetrievalEvidence] = Field(default_factory=list)
    intent: str | None = None
    route: str | None = None
    risk_level: str | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    draft_answer: str | None = None
    final_answer: str | None = None
    next_node: str | None = None


class AgentState(BaseModel):
    """贯穿客服工作流的唯一状态对象，也是检查点序列化边界。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[1] = STATE_SCHEMA_VERSION
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    thread_id: str
    request_id: str | None = None
    tenant_id: str | None = None
    shop_id: str | None = None
    conversation_id: str | None = None

    messages: list[StateMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    slots: dict[str, Any] = Field(default_factory=dict)
    tool_results: dict[str, Any] = Field(default_factory=dict)
    evidence: list[RetrievalEvidence] = Field(default_factory=list)

    intent: str | None = None
    route: str | None = None
    risk_level: str | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    draft_answer: str | None = None
    final_answer: str | None = None

    status: WorkflowStatus = WorkflowStatus.READY
    current_node: str | None = None
    next_node: str | None = None
    resume_from: str | None = None
    completed_nodes: list[str] = Field(default_factory=list)
    node_trace: list[NodeTrace] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    error: StateError | None = None

    revision: int = Field(default=0, ge=0)
    checkpoint_reason: str = "created"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def apply_patch(self, patch: StatePatch) -> None:
        """按稳定合并规则应用节点结果，防止节点覆盖其他节点的信息。"""
        self.messages.extend(patch.messages)
        self.context.update(patch.context)
        self.slots.update(patch.slots)
        self.tool_results.update(patch.tool_results)
        self.evidence.extend(patch.evidence)
        self.risk_reasons.extend(
            reason for reason in patch.risk_reasons if reason not in self.risk_reasons
        )
        for field_name in (
            "intent",
            "route",
            "risk_level",
            "draft_answer",
            "final_answer",
            "next_node",
        ):
            value = getattr(patch, field_name)
            if value is not None:
                setattr(self, field_name, value)

    def begin_node(self, node: str) -> None:
        """记录节点开始；中断时 resume_from 会指向该节点。"""
        if self.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.CANCELLED,
        }:
            raise ValueError(f"流程状态 {self.status} 不允许继续执行")
        attempt = sum(trace.node == node for trace in self.node_trace) + 1
        self.status = WorkflowStatus.RUNNING
        self.current_node = node
        self.resume_from = node
        self.error = None
        self.node_trace.append(NodeTrace(node=node, attempt=attempt))

    def complete_node(self, node: str, *, next_node: str | None = None) -> None:
        """完成当前节点并设置下一跳，最终节点可将 next_node 留空。"""
        trace = self._running_trace(node)
        trace.status = NodeStatus.COMPLETED
        trace.finished_at = utcnow()
        if node not in self.completed_nodes:
            self.completed_nodes.append(node)
        self.current_node = None
        self.next_node = next_node
        self.resume_from = next_node
        self.status = (
            WorkflowStatus.READY if next_node else WorkflowStatus.COMPLETED
        )

    def fail_node(
        self,
        node: str,
        *,
        code: str,
        retryable: bool,
        message: str = "节点执行失败",
    ) -> None:
        """记录安全错误；仅可重试错误会保留自动恢复入口。"""
        trace = self._running_trace(node)
        trace.status = NodeStatus.FAILED
        trace.finished_at = utcnow()
        trace.error_code = code
        self.error = StateError(
            node=node,
            code=code,
            message=message,
            retryable=retryable,
        )
        self.current_node = None
        self.resume_from = node if retryable else None
        self.status = (
            WorkflowStatus.FAILED if retryable else WorkflowStatus.WAITING_MANUAL
        )

    def prepare_action(
        self,
        action_type: str,
        idempotency_key: str,
        *,
        reference_id: str | None = None,
    ) -> None:
        """在调用外部系统前登记动作，检查点成功后才能真正执行。"""
        self.pending_action = PendingAction(
            action_type=action_type,
            idempotency_key=idempotency_key,
            reference_id=reference_id,
        )

    def start_action(self) -> None:
        """标记外部动作已开始；此状态下崩溃必须人工确认结果。"""
        action = self._require_action(ActionStatus.PREPARED)
        action.status = ActionStatus.EXECUTING
        action.started_at = utcnow()

    def finish_action(self) -> None:
        """外部系统明确确认成功后记录完成。"""
        action = self._require_action(ActionStatus.EXECUTING)
        action.status = ActionStatus.SUCCEEDED
        action.finished_at = utcnow()

    def mark_action_uncertain(self) -> None:
        """将无法确认的发送动作转人工，禁止自动重试。"""
        if self.pending_action is None:
            raise ValueError("当前没有待处理外部动作")
        self.pending_action.status = ActionStatus.UNCERTAIN
        self.pending_action.finished_at = utcnow()
        self.status = WorkflowStatus.WAITING_MANUAL
        self.resume_from = None
        self.error = StateError(
            node=self.current_node or "external_action",
            code="EXTERNAL_ACTION_UNCERTAIN",
            message="外部动作结果无法确认，需要人工核对",
            retryable=False,
        )

    def _running_trace(self, node: str) -> NodeTrace:
        for trace in reversed(self.node_trace):
            if trace.node == node and trace.status == NodeStatus.RUNNING:
                return trace
        raise ValueError(f"节点 {node} 没有运行中的执行记录")

    def _require_action(self, expected: ActionStatus) -> PendingAction:
        action = self.pending_action
        if action is None or action.status != expected:
            current = action.status if action else None
            raise ValueError(f"外部动作状态应为 {expected}，当前为 {current}")
        return action
