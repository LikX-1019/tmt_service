"""客服 Agent 的 State Contract v2 与节点间增量更新模型。

State 保存“引用和运行状态”，不保存可实时查询的完整业务事实。商品完整资料、
完整聊天历史、长期记忆和 Tool 实例都由各自 Source of Truth 或 Registry 管理。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


STATE_SCHEMA_VERSION = 2


def utcnow() -> datetime:
    """返回带 UTC 时区的时间，保证检查点可跨时区恢复。"""
    return datetime.now(timezone.utc)


def _new_run_id() -> str:
    return str(uuid4())


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


ChatRoute = Literal[
    "faq",
    "rag",
    "tool",
    "human",
    "greeting",
    "small_talk",
    "empathy",
    "llm_fallback",
    "fallback",
    "product",
    "product_selection",
    "product_missing",
    "product_not_found",
    "product_link_invalid",
    "product_link_unsupported",
]
RiskLevel = Literal["low", "medium", "high"]
ServiceStage = Literal["pre_sale", "post_sale", "general"]


class StateMessage(BaseModel):
    """跨节点和跨渠道传递的标准消息引用或短内容。

    完整聊天历史的 Source of Truth 是 MySQL messages 表；State 只应保存执行当前
    Turn 必需的消息或最近消息 ID，不应无限追加完整历史。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_new_run_id)
    session_id: str | None = None
    turn_id: str | None = None
    role: Literal["system", "customer", "assistant", "tool", "human"]
    kind: Literal["text", "image", "product", "tool_result", "system_event"] = "text"
    content: str | None = None
    platform_message_id: str | None = None
    reply_to_message_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalEvidence(BaseModel):
    """RAG 节点写入的精简证据，供决策、回复和审计继续使用。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    chunk_id: str | None = None
    content: str
    source: str | None = None
    product_id: str | None = None
    dense_score: float | None = Field(default=None, ge=0)
    bm25_score: float | None = Field(default=None, ge=0)
    fusion_score: float | None = Field(default=None, ge=0)
    rerank_score: float | None = Field(default=None, ge=0)
    score: float | None = Field(default=None, ge=0)
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
    attempt: int = Field(ge=1)
    status: NodeStatus = NodeStatus.RUNNING
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    error_code: str | None = None


class PendingAction(BaseModel):
    """需要外部提交的动作及其幂等信息。

    ``EXECUTING`` 崩溃后必须进入 ``UNCERTAIN`` 并转人工，不能自动重试，防止
    重复发送、重复退款或重复取消订单。
    """

    model_config = ConfigDict(extra="forbid")

    action_type: str
    idempotency_key: str
    status: ActionStatus = ActionStatus.PREPARED
    reference_id: str | None = None
    prepared_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ProductReference(BaseModel):
    """会话当前商品的稳定引用，不保存完整商品资料。"""

    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    sku_id: str | None = None
    source: Literal[
        "platform_card",
        "customer_selected",
        "message_extraction",
        "manual",
    ] = "customer_selected"
    confidence: float | None = Field(default=None, ge=0, le=1)
    selected_at: datetime = Field(default_factory=utcnow)


class ResolvedProductContext(BaseModel):
    """当前 Turn 执行期间的商品快照，由商品 Source of Truth 实时解析生成。

    该模型不是 Session 长期状态；下一轮商品问题应重新解析或基于版本谨慎复用。
    """

    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    sku_id: str | None = None
    name: str = Field(min_length=1)
    category: str | None = None
    summary: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    specifications: dict[str, str] = Field(default_factory=dict)
    usage: list[str] = Field(default_factory=list)
    suitable_for: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    after_sales_limits: list[str] = Field(default_factory=list)
    source_version: str | None = None
    source_updated_at: datetime | None = None
    resolved_at: datetime = Field(default_factory=utcnow)


class ProductResolutionState(BaseModel):
    """当前 Turn 的商品解析进度与安全失败状态。"""

    model_config = ConfigDict(extra="forbid")

    reference: ProductReference | None = None
    status: Literal[
        "not_required",
        "pending",
        "resolved",
        "not_found",
        "failed",
        "selection_required",
        "missing",
        "invalid_link",
        "unsupported_link",
    ] = "not_required"
    context: ResolvedProductContext | None = None
    recent_products: list[ConversationProductReferenceState] = Field(
        default_factory=list
    )
    profile: dict[str, Any] = Field(default_factory=dict)
    resolution_source: Literal[
        "request",
        "url",
        "message_id",
        "name_exact",
        "name_unique_contains",
        "history",
        "history_semantic",
        "name_candidates",
        "conversation",
        "none",
    ] = "none"
    error_code: str | None = None


class ConversationProductReferenceState(BaseModel):
    """有界最近商品引用；名称只用于回指解析，不是完整商品事实。"""

    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)


class MemorySnippet(BaseModel):
    """本轮加载到 State 的长期记忆片段；完整记忆由 Memory Store 管理。"""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    memory_type: str
    content: str
    confidence: float | None = Field(default=None, ge=0, le=1)


class ShortTermMemoryState(BaseModel):
    """短期记忆的引用和摘要，不保存无限完整消息历史。"""

    model_config = ConfigDict(extra="forbid")

    recent_message_ids: list[str] = Field(default_factory=list)
    conversation_summary: str | None = None
    last_user_message_id: str | None = None
    last_assistant_message_id: str | None = None
    max_recent_messages: int = Field(default=10, ge=1, le=100)
    recent_turns: list[RecentTurnState] = Field(default_factory=list)


class RecentTurnState(BaseModel):
    """有界历史问答投影；完整历史 Source of Truth 仍在 MySQL。"""

    model_config = ConfigDict(extra="forbid")

    customer: str | None = None
    assistant: str | None = None


class LongTermMemoryState(BaseModel):
    """长期记忆的加载状态，不复制 Memory Store 的全部内容。"""

    model_config = ConfigDict(extra="forbid")

    loaded_memory_ids: list[str] = Field(default_factory=list)
    retrieved_memories: list[MemorySnippet] = Field(default_factory=list)
    loaded_at: datetime | None = None


class RetrievalContext(BaseModel):
    """RAG 检索前的稳定过滤条件，优先使用 ID 而非展示名称。"""

    model_config = ConfigDict(extra="forbid")

    product_id: str | None = None
    sku_id: str | None = None
    service_stage: ServiceStage | None = None
    knowledge_version: str | None = None


class RetrievalState(BaseModel):
    """当前 Turn 的 RAG 检索过程和候选证据状态。"""

    model_config = ConfigDict(extra="forbid")

    context: RetrievalContext = Field(default_factory=RetrievalContext)
    candidates: list[RetrievalEvidence] = Field(default_factory=list)
    dense_count: int = Field(default=0, ge=0)
    bm25_count: int = Field(default=0, ge=0)
    fusion_count: int = Field(default=0, ge=0)
    rerank_count: int = Field(default=0, ge=0)
    top_score: float | None = Field(default=None, ge=0)
    score_margin: float | None = Field(default=None, ge=0)
    evidence_sufficient: bool = False


class HumanState(BaseModel):
    """人工介入状态，独立于 ``route=human`` 表达完整生命周期。"""

    model_config = ConfigDict(extra="forbid")

    required: bool = False
    status: Literal["none", "requested", "waiting", "active", "resolved"] = "none"
    reason_code: str | None = None
    reason: str | None = None
    requested_at: datetime | None = None
    assigned_agent_id: str | None = None
    resume_allowed: bool = True


class ToolDescriptor(BaseModel):
    """可序列化 Tool 描述；Tool 实例只能由 ToolRegistry 持有。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    category: str | None = None
    enabled: bool = True
    requires_confirmation: bool = False
    risk_level: Literal["low", "medium", "high"] = "low"


class ToolState(BaseModel):
    """当前 Turn 的 Tool 选择、参数追问和执行状态；大结果只保存引用。"""

    model_config = ConfigDict(extra="forbid")

    available_tools: list[ToolDescriptor] = Field(default_factory=list)
    selected_tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    missing_arguments: list[str] = Field(default_factory=list)
    status: Literal[
        "idle",
        "selecting",
        "waiting_arguments",
        "ready",
        "running",
        "succeeded",
        "failed",
    ] = "idle"
    result_ref: str | None = None


class WorkflowState(BaseModel):
    """可检查点的 Workflow 执行状态，保留 revision、resume 和副作用语义。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    run_id: str
    status: WorkflowStatus = WorkflowStatus.READY
    current_node: str | None = None
    next_node: str | None = None
    resume_from: str | None = None
    completed_nodes: list[str] = Field(default_factory=list)
    node_trace: list[NodeTrace] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=2, ge=0)
    revision: int = Field(default=0, ge=0)
    checkpoint_reason: str = "created"
    pending_action: PendingAction | None = None
    error: StateError | None = None


class ChatSessionState(BaseModel):
    """跨 Turn 的会话引用与长期运行状态。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[2] = STATE_SCHEMA_VERSION
    session_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    channel: Literal["customer_demo", "pdd", "taobao", "douyin", "other"]
    tenant_id: str | None = None
    shop_id: str | None = None
    customer_id: str | None = None
    current_product: ProductReference | None = None
    recent_product_ids: list[str] = Field(default_factory=list)
    service_stage: ServiceStage = "general"
    short_term_memory: ShortTermMemoryState = Field(
        default_factory=ShortTermMemoryState
    )
    long_term_memory: LongTermMemoryState = Field(default_factory=LongTermMemoryState)
    human: HumanState = Field(default_factory=HumanState)
    workflow: WorkflowState = Field(
        default_factory=lambda: WorkflowState(run_id=_new_run_id())
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def keep_product_references_consistent(self) -> ChatSessionState:
        if (
            self.current_product is not None
            and self.current_product.product_id not in self.recent_product_ids
        ):
            self.recent_product_ids.append(self.current_product.product_id)
        return self


class ChatTurnState(BaseModel):
    """单次用户请求的处理状态；商品快照和 RAG 证据只属于当前 Turn。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[2] = STATE_SCHEMA_VERSION
    turn_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    request_id: str | None = None
    input_message_id: str = Field(min_length=1)
    output_message_id: str | None = None
    original_query: str
    rewritten_query: str | None = None
    rewrite_applied: bool = False
    product: ProductResolutionState = Field(default_factory=ProductResolutionState)
    intent: str | None = None
    route: ChatRoute | None = None
    retrieval: RetrievalState = Field(default_factory=RetrievalState)
    tool: ToolState = Field(default_factory=ToolState)
    human: HumanState = Field(default_factory=HumanState)
    draft_answer: str | None = None
    final_answer: str | None = None
    fallback_reason: str | None = None
    risk_level: Literal["low", "medium", "high"] | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    workflow: WorkflowState
    reply: AgentReplyState | None = None
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class AgentProductCandidateState(BaseModel):
    """Graph 内部商品候选；由 API adapter 映射为响应视图。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    summary: str
    internal_code: str | None = None
    specifications: dict[str, str] = Field(default_factory=dict)


class AgentSourceState(BaseModel):
    """Graph 内部 QA 证据摘要。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str | None = None
    title: str | None = None
    source: str | None = None
    score: float | None = None


class AgentReplyState(BaseModel):
    """Turn 级最终回复契约；Graph 不直接依赖 API ChatResponse。"""

    model_config = ConfigDict(extra="forbid")

    source: Literal["rule", "product", "product_selection", "qa", "llm_fallback"]
    route: str | None = None
    answer: str
    product_id: str | None = None
    product_name: str | None = None
    products: list[AgentProductCandidateState] = Field(default_factory=list)
    product_resolution: Literal[
        "request",
        "url",
        "message_id",
        "name_exact",
        "name_unique_contains",
        "history",
        "history_semantic",
        "name_candidates",
        "conversation",
        "none",
    ] = "none"
    rule_name: str | None = None
    reason_code: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    qa_hit: bool | None = None
    sources: list[AgentSourceState] = Field(default_factory=list)


class StatePatch(BaseModel):
    """节点返回的增量结果。

    ``context``、``slots`` 和 ``tool_results`` 是 v1 兼容字段；新节点应优先
    使用 ``current_product``、``product``、``retrieval``、``tool``、``human``
    和 ``workflow`` 强类型分区。
    """

    model_config = ConfigDict(extra="forbid")

    messages: list[StateMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    slots: dict[str, Any] = Field(default_factory=dict)
    tool_results: dict[str, Any] = Field(default_factory=dict)
    evidence: list[RetrievalEvidence] = Field(default_factory=list)

    current_product: ProductReference | None = None
    product: ProductResolutionState | None = None
    retrieval: RetrievalState | None = None
    tool: ToolState | None = None
    human: HumanState | None = None
    workflow: WorkflowState | None = None
    recent_turns: list[RecentTurnState] | None = None
    reply: AgentReplyState | None = None

    intent: str | None = None
    route: ChatRoute | None = None
    risk_level: RiskLevel | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    draft_answer: str | None = None
    final_answer: str | None = None
    fallback_reason: str | None = None
    next_node: str | None = None


class AgentState(BaseModel):
    """v1 调用方兼容的检查点信封，并可组合 v2 Session/Turn 契约。

    当前 ``StateCoordinator`` 和 ``FileCheckpointStore`` 直接使用顶层字段；为了
    不一次性破坏已工作的 Console/PDD 恢复链路，v1 字段保留，``session`` 和
    ``turn`` 先作为强类型 v2 组合区。新代码应逐步迁移到这两个字段。
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[2] = STATE_SCHEMA_VERSION
    run_id: str = Field(default_factory=_new_run_id)
    thread_id: str
    request_id: str | None = None
    tenant_id: str | None = None
    shop_id: str | None = None
    conversation_id: str | None = None

    session: ChatSessionState | None = None
    turn: ChatTurnState | None = None

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
    fallback_reason: str | None = None

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

    @field_validator("schema_version", mode="before")
    @classmethod
    def migrate_v1_schema(cls, value: object) -> object:
        """允许读取既有 v1 检查点，在内存中升级为 v2 契约。"""
        return 2 if value == 1 else value

    @model_validator(mode="after")
    def validate_contract_references(self) -> AgentState:
        if self.session is not None and self.session.thread_id != self.thread_id:
            raise ValueError("session.thread_id 与 AgentState.thread_id 不一致")
        if self.turn is not None:
            if (
                self.session is not None
                and self.turn.session_id != self.session.session_id
            ):
                raise ValueError("turn.session_id 与 session.session_id 不一致")
            if self.turn.run_id != self.run_id:
                raise ValueError("turn.run_id 与 AgentState.run_id 不一致")
        return self

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

        if patch.current_product is not None:
            if self.session is None:
                raise ValueError("更新 current_product 前必须挂载 ChatSessionState")
            self.session.current_product = patch.current_product
        if patch.product is not None:
            if self.turn is None:
                raise ValueError("更新 product 前必须挂载 ChatTurnState")
            self.turn.product = patch.product
        if patch.retrieval is not None:
            if self.turn is None:
                raise ValueError("更新 retrieval 前必须挂载 ChatTurnState")
            self.turn.retrieval = patch.retrieval
        if patch.reply is not None:
            if self.turn is None:
                raise ValueError("更新 reply 前必须挂载 ChatTurnState")
            self.turn.reply = patch.reply
        if patch.tool is not None:
            if self.turn is None:
                raise ValueError("更新 tool 前必须挂载 ChatTurnState")
            self.turn.tool = patch.tool
        if patch.human is not None:
            if self.session is None:
                raise ValueError("更新 human 前必须挂载 ChatSessionState")
            self.session.human = patch.human
            if self.turn is not None:
                self.turn.human = patch.human
        if patch.workflow is not None:
            self._apply_workflow_patch(patch.workflow)
        if patch.recent_turns is not None:
            if self.session is None:
                raise ValueError("更新 recent_turns 前必须挂载 ChatSessionState")
            self.session.short_term_memory.recent_turns = patch.recent_turns

        for field_name in (
            "intent",
            "route",
            "risk_level",
            "draft_answer",
            "final_answer",
            "fallback_reason",
            "next_node",
        ):
            value = getattr(patch, field_name)
            if value is not None:
                setattr(self, field_name, value)

        self.sync_contract_state()

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
        self.sync_contract_state()

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
        self.status = WorkflowStatus.READY if next_node else WorkflowStatus.COMPLETED
        self.sync_contract_state()

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
        self.sync_contract_state()

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
        self.sync_contract_state()

    def start_action(self) -> None:
        """标记外部动作已开始；此状态下崩溃必须人工确认结果。"""
        action = self._require_action(ActionStatus.PREPARED)
        action.status = ActionStatus.EXECUTING
        action.started_at = utcnow()
        self.sync_contract_state()

    def finish_action(self) -> None:
        """外部系统明确确认成功后记录完成。"""
        action = self._require_action(ActionStatus.EXECUTING)
        action.status = ActionStatus.SUCCEEDED
        action.finished_at = utcnow()
        self.sync_contract_state()

    def mark_action_uncertain(self) -> None:
        """将无法确认的外部动作转人工，禁止自动重试。"""
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
        if self.session is not None:
            self.session.human = HumanState(
                required=True,
                status="waiting",
                reason_code="EXTERNAL_ACTION_UNCERTAIN",
                reason="外部动作结果无法确认，需要人工核对",
            )
        self.sync_contract_state()

    def sync_contract_state(self) -> None:
        """把兼容顶层执行状态同步到已挂载的 v2 Session/Turn。"""
        workflow = self._workflow_snapshot()
        if self.turn is not None:
            self.turn.workflow = workflow
            self.turn.intent = self.intent
            self.turn.route = cast(ChatRoute, self.route)
            self.turn.risk_level = cast(RiskLevel, self.risk_level)
            self.turn.risk_reasons = self.risk_reasons
            self.turn.draft_answer = self.draft_answer
            self.turn.final_answer = self.final_answer
        if self.session is not None:
            self.session.workflow = workflow
            self.session.updated_at = self.updated_at

    def _workflow_snapshot(self) -> WorkflowState:
        return WorkflowState(
            run_id=self.run_id,
            status=self.status,
            current_node=self.current_node,
            next_node=self.next_node,
            resume_from=self.resume_from,
            completed_nodes=self.completed_nodes,
            node_trace=self.node_trace,
            revision=self.revision,
            checkpoint_reason=self.checkpoint_reason,
            pending_action=self.pending_action,
            error=self.error,
        )

    def _apply_workflow_patch(self, workflow: WorkflowState) -> None:
        if workflow.run_id != self.run_id:
            raise ValueError("workflow.run_id 与 AgentState.run_id 不一致")
        self.status = workflow.status
        self.current_node = workflow.current_node
        self.next_node = workflow.next_node
        self.resume_from = workflow.resume_from
        self.completed_nodes = workflow.completed_nodes
        self.node_trace = workflow.node_trace
        self.revision = workflow.revision
        self.checkpoint_reason = workflow.checkpoint_reason
        self.pending_action = workflow.pending_action
        self.error = workflow.error

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
