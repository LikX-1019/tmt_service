"""State Contract v2 serialization, boundary, and compatibility tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.checkpoint import FileCheckpointStore
from app.agent.coordinator import StateCoordinator
from app.agent.state import (
    ActionStatus,
    AgentState,
    ChatSessionState,
    ChatTurnState,
    HumanState,
    LongTermMemoryState,
    MemorySnippet,
    PendingAction,
    ProductReference,
    ProductResolutionState,
    ResolvedProductContext,
    RetrievalContext,
    RetrievalEvidence,
    RetrievalState,
    ShortTermMemoryState,
    StatePatch,
    ToolDescriptor,
    ToolState,
    WorkflowState,
    WorkflowStatus,
)


def make_session(session_id: str = "session-1") -> ChatSessionState:
    return ChatSessionState(
        session_id=session_id,
        thread_id="conversation-1",
        channel="pdd",
        tenant_id="tenant-1",
        shop_id="shop-1",
        customer_id="customer-1",
    )


def make_turn(
    session: ChatSessionState,
    *,
    run_id: str,
    turn_id: str = "turn-1",
) -> ChatTurnState:
    return ChatTurnState(
        turn_id=turn_id,
        session_id=session.session_id,
        run_id=run_id,
        input_message_id="message-in-1",
        original_query="这个怎么使用？",
        workflow=WorkflowState(run_id=run_id),
    )


def make_agent() -> AgentState:
    agent = AgentState(thread_id="conversation-1", run_id="run-1")
    session = make_session()
    turn = make_turn(session, run_id=agent.run_id)
    agent.session = session
    agent.turn = turn
    agent.sync_contract_state()
    return agent


def resolved_product() -> ResolvedProductContext:
    return ResolvedProductContext(
        product_id="product-1",
        sku_id="sku-1",
        name="护腕",
        category="运动护具",
        summary="日常佩戴支撑",
        selling_points=["支撑"],
        specifications={"尺寸": "L"},
        usage=["白天佩戴"],
        suitable_for=["运动人群"],
        warnings=["皮肤破损时停用"],
        after_sales_limits=["不影响七天无理由"],
        source_version="v3",
    )


def test_chat_session_state_round_trips_json() -> None:
    session = make_session()
    session.current_product = ProductReference(
        product_id="product-1",
        sku_id="sku-1",
        source="platform_card",
        confidence=0.98,
    )
    session.short_term_memory = ShortTermMemoryState(
        recent_message_ids=["message-1", "message-2"],
        conversation_summary="顾客咨询护腕用法",
        last_user_message_id="message-2",
        max_recent_messages=8,
    )

    restored = ChatSessionState.model_validate_json(session.model_dump_json())

    assert restored == session
    assert restored.current_product is not None
    assert restored.current_product.product_id == "product-1"
    assert restored.current_product.sku_id == "sku-1"
    assert restored.recent_product_ids == ["product-1"]
    assert restored.short_term_memory.recent_message_ids == [
        "message-1",
        "message-2",
    ]


def test_chat_turn_state_round_trips_json() -> None:
    session = make_session()
    turn = make_turn(session, run_id="run-1")
    turn.product = ProductResolutionState(
        reference=ProductReference(product_id="product-1"),
        status="resolved",
        context=resolved_product(),
    )
    turn.retrieval = RetrievalState(
        context=RetrievalContext(
            product_id="product-1",
            sku_id="sku-1",
            service_stage="pre_sale",
            knowledge_version="v2026-09",
        ),
        candidates=[
            RetrievalEvidence(
                document_id="qa-1",
                chunk_id="chunk-1",
                content="问题：怎么使用\n回答：白天佩戴",
                source="cs_qa",
                product_id="product-1",
                dense_score=0.91,
                bm25_score=8.2,
                fusion_score=0.03,
                rerank_score=0.97,
            )
        ],
        dense_count=10,
        bm25_count=9,
        fusion_count=10,
        rerank_count=1,
        top_score=0.97,
        score_margin=0.2,
        evidence_sufficient=True,
    )
    turn.tool = ToolState(
        available_tools=[
            ToolDescriptor(
                name="order_lookup",
                category="order",
                requires_confirmation=False,
                risk_level="low",
            )
        ],
        status="idle",
    )

    restored = ChatTurnState.model_validate_json(turn.model_dump_json())

    assert restored == turn
    assert restored.product.context is not None
    assert restored.product.context.name == "护腕"
    assert restored.retrieval.context.product_id == "product-1"
    assert restored.retrieval.candidates[0].rerank_score == 0.97


def test_product_reference_is_only_a_reference() -> None:
    product = ProductReference(
        product_id="product-1",
        source="message_extraction",
        confidence=0.87,
    )

    payload = product.model_dump(mode="json")

    assert payload["product_id"] == "product-1"
    assert set(payload) == {
        "product_id",
        "sku_id",
        "source",
        "confidence",
        "selected_at",
    }
    assert "name" not in payload
    assert "selling_points" not in payload
    assert "specifications" not in payload
    assert "usage" not in payload
    assert "warnings" not in payload
    assert "after_sales_limits" not in payload


@pytest.mark.parametrize(
    ("status", "context"),
    [
        ("pending", None),
        (
            "resolved",
            ProductResolutionState(
                reference=ProductReference(product_id="product-1"),
                status="resolved",
                context=resolved_product(),
            ).context,
        ),
        ("not_found", None),
        ("failed", None),
    ],
)
def test_product_resolution_state_statuses(status: str, context: Any) -> None:
    resolution = ProductResolutionState(
        reference=ProductReference(product_id="product-1"),
        status=status,
        context=context,
        error_code=None if status != "failed" else "PRODUCT_PROVIDER_TIMEOUT",
    )

    assert resolution.status == status


def test_session_can_switch_current_product_and_serialize() -> None:
    session = make_session()
    first = ProductReference(product_id="product-1", source="platform_card")
    second = ProductReference(product_id="product-2", source="customer_selected")

    session.current_product = first
    session.current_product = second

    restored = ChatSessionState.model_validate_json(session.model_dump_json())
    assert restored.current_product is not None
    assert restored.current_product.product_id == "product-2"
    assert restored.recent_product_ids == ["product-1", "product-2"]


def test_short_term_memory_keeps_only_message_ids() -> None:
    memory = ShortTermMemoryState(
        recent_message_ids=["m1", "m2", "m3"],
        conversation_summary="顾客咨询尺寸",
        last_user_message_id="m3",
        last_assistant_message_id="m2",
        max_recent_messages=3,
    )

    payload = memory.model_dump(mode="json")

    assert payload["recent_message_ids"] == ["m1", "m2", "m3"]
    assert all(not key.startswith("content") for key in payload)


def test_long_term_memory_keeps_loaded_references_not_full_store() -> None:
    memory = LongTermMemoryState(
        loaded_memory_ids=["memory-1", "memory-2", "memory-3"],
        retrieved_memories=[
            MemorySnippet(
                memory_id="memory-1",
                memory_type="preference",
                content="偏好黑色",
                confidence=0.9,
            )
        ],
    )

    assert len(memory.loaded_memory_ids) == 3
    assert len(memory.retrieved_memories) == 1


def test_tool_descriptor_and_state_round_trip() -> None:
    tool = ToolState(
        available_tools=[
            ToolDescriptor(
                name="refund_create",
                category="after_sales",
                requires_confirmation=True,
                risk_level="high",
            )
        ],
        selected_tool="refund_create",
        arguments={"order_id": "order-1"},
        missing_arguments=["reason_code"],
        status="waiting_arguments",
    )

    restored = ToolState.model_validate_json(tool.model_dump_json())

    assert restored == tool
    assert restored.available_tools[0].risk_level == "high"


def test_human_state_uses_contract_statuses() -> None:
    human = HumanState(
        required=True,
        status="waiting",
        reason_code="RAG_EVIDENCE_INSUFFICIENT",
        reason="证据不足",
        resume_allowed=False,
    )
    restored = HumanState.model_validate_json(human.model_dump_json())

    assert restored == human


def test_workflow_state_resume_and_retry_fields() -> None:
    workflow = WorkflowState(
        run_id="run-1",
        status=WorkflowStatus.FAILED,
        current_node=None,
        next_node="rewrite",
        resume_from="rewrite",
        completed_nodes=["intent"],
        retry_count=1,
        max_retries=3,
        revision=4,
        checkpoint_reason="failed:rewrite",
    )
    restored = WorkflowState.model_validate_json(workflow.model_dump_json())

    assert restored == workflow
    assert restored.resume_from == "rewrite"
    assert restored.retry_count <= restored.max_retries


def test_pending_action_status_semantics_remain_strict() -> None:
    action = PendingAction(
        action_type="pdd_send_message",
        idempotency_key="outbound-1",
    )
    assert action.status == ActionStatus.PREPARED

    action.status = ActionStatus.EXECUTING
    action.status = ActionStatus.SUCCEEDED

    uncertain = PendingAction(
        action_type="pdd_send_message",
        idempotency_key="outbound-2",
    )
    uncertain.status = ActionStatus.UNCERTAIN
    assert uncertain.status == ActionStatus.UNCERTAIN


@pytest.mark.asyncio
async def test_file_checkpoint_store_persists_v2_session_and_turn(
    tmp_path: Path,
) -> None:
    store = FileCheckpointStore(tmp_path)
    state = make_agent()

    await store.save(state, reason="created")
    restored = await FileCheckpointStore(tmp_path).load(state.run_id)

    assert restored is not None
    assert restored.schema_version == 2
    assert restored.session is not None
    assert restored.turn is not None
    assert restored.revision == 1
    assert restored.session.workflow.revision == 1
    assert restored.turn.workflow.revision == 1
    assert restored.turn.workflow.checkpoint_reason == "created"


def test_agent_state_reads_v1_checkpoint_shape() -> None:
    legacy_json = """
    {
      "schema_version": 1,
      "run_id": "legacy-run",
      "thread_id": "conversation-legacy",
      "evidence": [
        {"document_id": "doc-1", "content": "旧证据", "score": 0.8}
      ],
      "route": "legacy_route",
      "risk_level": "legacy-risk",
      "revision": 3
    }
    """

    state = AgentState.model_validate_json(legacy_json)

    assert state.schema_version == 2
    assert state.session is None
    assert state.turn is None
    assert state.evidence[0].score == 0.8
    assert state.route == "legacy_route"
    assert state.risk_level == "legacy-risk"


@pytest.mark.asyncio
async def test_coordinator_applies_typed_patch_to_v2_sections(
    tmp_path: Path,
) -> None:
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    state = make_agent()
    retrieval = RetrievalState(
        context=RetrievalContext(product_id="product-1"),
        evidence_sufficient=True,
    )
    human = HumanState(required=True, status="requested", reason_code="RISK_RULE")

    async def rag_node(_state: AgentState) -> StatePatch:
        return StatePatch(
            current_product=ProductReference(product_id="product-1"),
            product=ProductResolutionState(
                reference=ProductReference(product_id="product-1"),
                status="resolved",
                context=resolved_product(),
            ),
            retrieval=retrieval,
            tool=ToolState(status="idle"),
            human=human,
            route="rag",
            next_node="answer",
        )

    await coordinator.create(state)
    result = await coordinator.run_node(state, "rag", rag_node)

    assert result.turn is not None
    assert result.session is not None
    assert result.session.current_product is not None
    assert result.session.current_product.product_id == "product-1"
    assert result.turn.product.status == "resolved"
    assert result.turn.retrieval == retrieval
    assert result.turn.tool.status == "idle"
    assert result.turn.human == human
    assert result.turn.route == "rag"
    assert result.turn.workflow.next_node == "answer"


@pytest.mark.asyncio
async def test_uncertain_action_syncs_session_human_state(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    state = make_agent()
    await coordinator.create(state)
    state.begin_node("send_reply")
    await coordinator.checkpoint_action_prepared(
        state,
        action_type="pdd_send_message",
        idempotency_key="outbound-1",
    )
    await coordinator.checkpoint_action_started(state)

    restored = await coordinator.load_for_resume(state.run_id)

    assert restored is not None
    assert restored.session is not None
    assert restored.session.human.required is True
    assert restored.session.human.status == "waiting"
    assert restored.turn is not None
    assert restored.turn.workflow.pending_action is not None
    assert restored.turn.workflow.pending_action.status == ActionStatus.UNCERTAIN


@pytest.mark.parametrize(
    "model",
    [
        ChatSessionState,
        ChatTurnState,
        ProductReference,
        ProductResolutionState,
        ResolvedProductContext,
        ShortTermMemoryState,
        LongTermMemoryState,
        RetrievalContext,
        RetrievalState,
        RetrievalEvidence,
        HumanState,
        ToolDescriptor,
        ToolState,
        WorkflowState,
        PendingAction,
        StatePatch,
    ],
)
def test_state_contract_models_forbid_extra_fields(model: type[Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"unexpected_field": "value"})


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (
            lambda: ProductReference(product_id="p", confidence=1.01),
            "confidence",
        ),
        (
            lambda: MemorySnippet(
                memory_id="m", memory_type="pref", content="x", confidence=2
            ),
            "confidence",
        ),
        (lambda: ToolDescriptor(name="t", risk_level="critical"), "risk_level"),
        (lambda: HumanState(status="unknown"), "status"),
        (
            lambda: ChatSessionState(session_id="s", thread_id="t", channel="email"),
            "channel",
        ),
    ],
)
def test_invalid_confidence_risk_and_status_are_rejected(
    factory: Any, field: str
) -> None:
    with pytest.raises(ValidationError) as error:
        factory()

    assert field in str(error.value)
