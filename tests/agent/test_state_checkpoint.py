from pathlib import Path

import pytest

from app.agent.checkpoint import CheckpointConflictError, FileCheckpointStore
from app.agent.coordinator import StateCoordinator
from app.agent.state import (
    ActionStatus,
    AgentState,
    StateMessage,
    StatePatch,
    WorkflowStatus,
)


def test_state_patch_merges_information_without_overwriting() -> None:
    state = AgentState(
        thread_id="conversation-1",
        context={"goods_id": "sku-1"},
        slots={"color": "black"},
    )

    state.apply_patch(
        StatePatch(
            messages=[StateMessage(role="customer", content="适合跑步吗")],
            context={"goods_name": "护膝"},
            slots={"size": "L"},
            intent="product_consulting",
            next_node="rag",
        )
    )

    assert state.context == {"goods_id": "sku-1", "goods_name": "护膝"}
    assert state.slots == {"color": "black", "size": "L"}
    assert state.messages[0].content == "适合跑步吗"
    assert state.intent == "product_consulting"
    assert state.next_node == "rag"


@pytest.mark.asyncio
async def test_file_checkpoint_survives_store_recreation(tmp_path: Path) -> None:
    state = AgentState(thread_id="conversation-1")
    first_store = FileCheckpointStore(tmp_path)
    await first_store.save(state, reason="created")

    restored = await FileCheckpointStore(tmp_path).load(state.run_id)

    assert restored is not None
    assert restored.run_id == state.run_id
    assert restored.revision == 1
    assert restored.checkpoint_reason == "created"


@pytest.mark.asyncio
async def test_checkpoint_rejects_stale_revision(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    state = AgentState(thread_id="conversation-1")
    await store.save(state, reason="created")
    stale = state.model_copy(deep=True)

    await store.save(state, reason="newer")

    with pytest.raises(CheckpointConflictError):
        await store.save(stale, reason="stale")


@pytest.mark.asyncio
async def test_coordinator_recovers_interrupted_regular_node(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    state = AgentState(thread_id="conversation-1")
    await coordinator.create(state)
    state.begin_node("intent")
    await store.save(state, reason="before:intent")

    restored = await coordinator.load_for_resume(state.run_id)

    assert restored is not None
    assert restored.status == WorkflowStatus.READY
    assert restored.next_node == "intent"
    assert restored.current_node is None


@pytest.mark.asyncio
async def test_uncertain_external_action_never_auto_retries(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    state = AgentState(thread_id="conversation-1")
    await coordinator.create(state)
    state.begin_node("send_reply")
    await store.save(state, reason="before:send_reply")
    await coordinator.checkpoint_action_prepared(
        state,
        action_type="pdd_send_message",
        idempotency_key="outbound-job-1",
        reference_id="outbound-job-1",
    )
    await coordinator.checkpoint_action_started(state)

    restored = await coordinator.load_for_resume(state.run_id)

    assert restored is not None
    assert restored.status == WorkflowStatus.WAITING_MANUAL
    assert restored.resume_from is None
    assert restored.pending_action is not None
    assert restored.pending_action.status == ActionStatus.UNCERTAIN
    assert restored.error is not None
    assert restored.error.code == "EXTERNAL_ACTION_UNCERTAIN"


@pytest.mark.asyncio
async def test_successful_action_completes_node_in_same_checkpoint(
    tmp_path: Path,
) -> None:
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    state = AgentState(thread_id="conversation-1")
    await coordinator.create(state)
    state.begin_node("send_reply")
    await store.save(state, reason="before:send_reply")
    await coordinator.checkpoint_action_prepared(
        state,
        action_type="pdd_send_message",
        idempotency_key="outbound-job-1",
    )
    await coordinator.checkpoint_action_started(state)

    await coordinator.checkpoint_action_succeeded(state, next_node="audit")
    restored = await coordinator.load_for_resume(state.run_id)

    assert restored is not None
    assert restored.status == WorkflowStatus.READY
    assert restored.current_node is None
    assert restored.next_node == "audit"
    assert restored.completed_nodes == ["send_reply"]
    assert restored.pending_action is not None
    assert restored.pending_action.status == ActionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_coordinator_checkpoints_node_patch(tmp_path: Path) -> None:
    coordinator = StateCoordinator(FileCheckpointStore(tmp_path))
    state = AgentState(thread_id="conversation-1", next_node="intent")
    await coordinator.create(state)

    async def intent_node(_state: AgentState) -> StatePatch:
        return StatePatch(intent="faq", next_node="rag")

    result = await coordinator.run_node(state, "intent", intent_node)

    assert result.status == WorkflowStatus.READY
    assert result.intent == "faq"
    assert result.next_node == "rag"
    assert result.completed_nodes == ["intent"]
    assert result.revision == 3
