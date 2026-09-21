from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.chat_runtime import (
    ChatStateRuntime,
    product_profile_to_state_context,
    product_reference_from_resolution,
    retrieval_document_to_state_evidence,
)
from app.agent.checkpoint import FileCheckpointStore
from app.agent.coordinator import StateCoordinator
from app.agent.state import AgentState, WorkflowStatus
from app.core.exceptions import ProductServiceUnavailableError
from app.qa.models import QAResult, QASource, RetrievalDocument
from app.schemas.chat import ChatRequest
from app.services.product_service import ProductProfile
from tests.unit.test_chat_service import (
    FakeConversations,
    FakeProducts,
    FakeQA,
    FailingProducts,
    make_service,
)


pytestmark = pytest.mark.asyncio


class RecordingCheckpointStore(FileCheckpointStore):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.reasons: list[str] = []

    async def save(self, state: AgentState, *, reason: str) -> AgentState:
        self.reasons.append(reason)
        return await super().save(state, reason=reason)


async def _states(directory: Path) -> list[AgentState]:
    store = FileCheckpointStore(directory)
    states = []
    for path in sorted(directory.glob("*.json")):
        state = await store.load(path.stem)
        assert state is not None
        states.append(state)
    return states


def _runtime(directory: Path) -> ChatStateRuntime:
    return ChatStateRuntime(
        FileCheckpointStore(directory),
        cleanup_completed=False,
    )


async def test_chat_creates_session_and_new_turn_per_request(tmp_path: Path) -> None:
    service, _, _, _, _ = make_service(state_runtime=_runtime(tmp_path))

    await service.chat(ChatRequest(conversation_id="c1", customer_id="u1", message="你好"))
    await service.chat(ChatRequest(conversation_id="c1", customer_id="u1", message="谢谢"))

    states = {
        state.turn.original_query: state
        for state in await _states(tmp_path)
        if state.turn is not None
    }
    first, second = states["你好"], states["谢谢"]
    assert first.session is not None and first.turn is not None
    assert second.session is not None and second.turn is not None
    assert first.session.session_id == second.session.session_id == "c1"
    assert first.session.thread_id == second.session.thread_id == "c1"
    assert first.session.customer_id == second.session.customer_id == "u1"
    assert first.session.channel == second.session.channel == "other"
    assert first.session.service_stage == "general"
    assert first.turn.turn_id != second.turn.turn_id
    assert first.run_id != second.run_id
    assert first.run_id == first.turn.run_id
    assert first.turn.input_message_id == first.messages[0].id
    assert first.status == second.status == WorkflowStatus.COMPLETED
    assert first.turn.completed_at is not None


async def test_terminal_human_rule_completes_without_product_or_qa(
    tmp_path: Path,
) -> None:
    service, products, answers, qa, _ = make_service(state_runtime=_runtime(tmp_path))

    response = await service.chat(
        ChatRequest(conversation_id="c1", message="我要退款", product_id="1001")
    )

    [state] = await _states(tmp_path)
    assert response.route == "human"
    assert state.turn is not None and state.session is not None
    assert state.turn.route == "human"
    assert state.turn.human.required is True
    assert state.turn.human.status == "requested"
    assert state.turn.human.reason_code == "refund_request"
    assert state.session.human == state.turn.human
    assert state.turn.final_answer == response.answer
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 0


async def test_product_facts_are_turn_scoped_and_followup_reloads_source(
    tmp_path: Path,
) -> None:
    products = FakeProducts(
        {
            "TEST-WRIST-001": ProductProfile(
                id="TEST-WRIST-001",
                name="测试护腕",
                summary="稳定支撑。",
                selling_points=["透气"],
                specifications={"尺码": "均码"},
                usage="训练前佩戴",
                suitable_for="日常跑步",
                warnings="皮肤不适时停用",
                after_sales_limits="拆封后不可退",
                updated_at="2026-09-19T08:30:00Z",
            )
        }
    )
    service, products, _, _, _ = make_service(
        products=products,
        state_runtime=_runtime(tmp_path),
    )

    await service.chat(
        ChatRequest(
            conversation_id="c1",
            product_id="TEST-WRIST-001",
            message="这个怎么使用？",
        )
    )
    await service.chat(ChatRequest(conversation_id="c1", message="一天戴多久？"))

    states = {
        state.turn.original_query: state
        for state in await _states(tmp_path)
        if state.turn is not None
    }
    first, second = states["这个怎么使用？"], states["一天戴多久？"]
    assert products.calls == ["TEST-WRIST-001", "TEST-WRIST-001"]
    for state in (first, second):
        assert state.session is not None and state.turn is not None
        reference = state.session.current_product
        assert reference is not None
        assert reference.product_id == "TEST-WRIST-001"
        assert state.session.recent_product_ids == ["TEST-WRIST-001"]
        session_json = state.session.model_dump_json()
        assert "selling_points" not in session_json
        assert "after_sales_limits" not in session_json
        context = state.turn.product.context
        assert context is not None
        assert context.usage == ["训练前佩戴"]
        assert context.suitable_for == ["日常跑步"]
        assert context.warnings == ["皮肤不适时停用"]
        assert context.after_sales_limits == ["拆封后不可退"]
    assert second.session is not None
    assert second.session.current_product is not None
    assert second.session.current_product.source == "manual"


async def test_product_selection_and_not_found_have_stable_state_routes(
    tmp_path: Path,
) -> None:
    service, _, _, _, _ = make_service(state_runtime=_runtime(tmp_path))

    selection = await service.chat(
        ChatRequest(conversation_id="selection", message="我想看看 运动护膝")
    )
    missing = await service.chat(
        ChatRequest(conversation_id="missing", message="这个怎么样", product_id="999999")
    )

    states = {state.conversation_id: state for state in await _states(tmp_path)}
    selection_state = states["selection"]
    missing_state = states["missing"]
    assert selection.route == "product_selection"
    assert selection_state.turn is not None
    assert selection_state.turn.route == "product_selection"
    assert selection_state.turn.product.status == "pending"
    assert missing.route == "product_not_found"
    assert missing_state.turn is not None
    assert missing_state.turn.route == "product"
    assert missing_state.turn.product.status == "not_found"
    assert missing_state.turn.product.error_code == "product_not_found"


async def test_general_qa_hydrates_session_without_starting_product_turn(
    tmp_path: Path,
) -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("1001", "护腕")
    service, products, _, qa, _ = make_service(
        conversations=conversations,
        state_runtime=_runtime(tmp_path),
    )

    await service.chat(ChatRequest(conversation_id="c1", message="你们什么时候发货"))

    [state] = await _states(tmp_path)
    assert state.session is not None and state.turn is not None
    assert state.session.current_product is not None
    assert state.session.current_product.product_id == "1001"
    assert state.turn.product.status == "resolved"
    assert products.calls == ["1001"]
    assert qa.calls == 0


async def test_stale_binding_is_cleared_from_mysql_and_session(tmp_path: Path) -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("missing-product", "已下架商品")
    service, _, _, _, conversations = make_service(
        conversations=conversations,
        state_runtime=_runtime(tmp_path),
    )

    await service.chat(ChatRequest(conversation_id="c1", message="这个怎么用"))

    [state] = await _states(tmp_path)
    assert "c1" not in conversations.values
    assert state.session is not None
    assert state.session.current_product is None
    assert state.session.recent_product_ids == []
    assert state.turn is not None
    assert state.turn.product.status == "not_found"


@pytest.mark.parametrize(
    ("route", "expected_candidates", "expected_fallback"),
    [
        ("faq", 1, None),
        ("rag", 1, None),
        ("fallback", 1, None),
    ],
)
async def test_qa_result_updates_turn_retrieval_state(
    tmp_path: Path,
    route: str,
    expected_candidates: int,
    expected_fallback: str | None,
) -> None:
    document = RetrievalDocument(
        chunk_id="chunk-1",
        content="证据正文",
        source="cs_qa",
        metadata={"document_id": "doc-1", "product_id": "p1"},
        dense_score=0.7,
        bm25_score=0.6,
        fusion_score=0.8,
        rerank_score=0.9,
    )
    qa_result = QAResult(
        answer="QA 回答",
        route=route,  # type: ignore[arg-type]
        confidence=1.0 if route == "faq" else None,
        sources=(
            [QASource(chunk_id="chunk-1", source="cs_qa", score=0.9)]
            if route == "rag"
            else []
        ),
        trace_documents=[document],
        retrieval_counts={"dense": 4, "bm25": 3, "fusion": 2, "rerank": 1},
    )
    qa = FakeQA(qa_result)
    service, _, _, _, _ = make_service(
        qa=qa,
        state_runtime=_runtime(tmp_path / route),
    )

    response = await service.chat(ChatRequest(message="你们什么时候发货"))

    [state] = await _states(tmp_path / route)
    assert state.turn is not None
    assert state.turn.route == route
    assert state.turn.final_answer == response.answer
    assert len(state.turn.retrieval.candidates) == expected_candidates
    assert state.turn.retrieval.dense_count == 4
    assert state.turn.retrieval.bm25_count == 3
    assert state.turn.retrieval.fusion_count == 2
    assert state.turn.retrieval.rerank_count == 1
    assert state.turn.fallback_reason == expected_fallback
    if route == "rag":
        [evidence] = state.turn.retrieval.candidates
        assert evidence.document_id == "doc-1"
        assert evidence.rerank_score == 0.9
        assert state.turn.retrieval.top_score == 0.9
        assert state.turn.retrieval.evidence_sufficient is True


async def test_sessions_are_isolated_and_new_session_has_no_product(
    tmp_path: Path,
) -> None:
    service, _, _, _, _ = make_service(state_runtime=_runtime(tmp_path))

    await service.chat(
        ChatRequest(conversation_id="a", message="这个怎么样", product_id="1001")
    )
    await service.chat(
        ChatRequest(conversation_id="b", message="这个怎么样", product_id="2002")
    )
    await service.chat(ChatRequest(conversation_id="new", message="你好"))

    states = {state.conversation_id: state for state in await _states(tmp_path)}
    assert states["a"].session is not None
    assert states["b"].session is not None
    assert states["new"].session is not None
    assert states["a"].session.current_product is not None
    assert states["b"].session.current_product is not None
    assert states["a"].session.current_product.product_id == "1001"
    assert states["b"].session.current_product.product_id == "2002"
    assert states["new"].session.current_product is None


async def test_failed_chat_persists_safe_recoverable_state(tmp_path: Path) -> None:
    service, _, _, _, _ = make_service(
        products=FailingProducts({}),
        state_runtime=_runtime(tmp_path),
    )

    with pytest.raises(ProductServiceUnavailableError):
        await service.chat(
            ChatRequest(conversation_id="c1", message="这个怎么样", product_id="1001")
        )

    [state] = await _states(tmp_path)
    assert state.status == WorkflowStatus.FAILED
    assert state.error is not None
    assert state.error.code == "PRODUCTSERVICEUNAVAILABLEERROR"
    assert state.error.message == "节点执行失败"
    assert state.turn is not None
    assert state.turn.fallback_reason is None
    assert "provider unavailable" not in state.model_dump_json()


async def test_state_runtime_defers_checkpoint_ownership_to_agent_runtime(
    tmp_path: Path,
) -> None:
    store = FileCheckpointStore(tmp_path)
    runtime = ChatStateRuntime(store, cleanup_completed=False)
    state = runtime.create_state(
        message="这个怎么用",
        conversation_id="c1",
        customer_id="u1",
        service_stage="pre_sale",
    )
    runtime.hydrate_product_binding(state, "1001")
    await runtime.start(state)

    restored = await StateCoordinator(store).load_for_resume(state.run_id)

    assert restored is None
    assert state.status == WorkflowStatus.READY
    assert state.next_node == "session_hydrate"
    assert state.session is not None
    assert state.session.current_product is not None
    assert state.session.current_product.product_id == "1001"


async def test_completed_checkpoint_is_cleaned_up_by_default(tmp_path: Path) -> None:
    store = RecordingCheckpointStore(tmp_path)
    service, _, _, _, _ = make_service(
        state_runtime=ChatStateRuntime(store),
    )

    await service.chat(ChatRequest(conversation_id="c1", message="你好"))

    assert store.reasons[0] == "workflow_created"
    assert "before:session_hydrate" in store.reasons
    assert "after:response" in store.reasons
    assert store.reasons[-1] == "workflow_completed"
    assert all("chat_turn" not in reason for reason in store.reasons)
    assert list(tmp_path.glob("*.json")) == []


async def test_state_mappers_are_explicit_and_tolerate_invalid_product_time() -> None:
    request_reference = product_reference_from_resolution("p1", "request")
    url_reference = product_reference_from_resolution("p1", "url")
    binding_reference = product_reference_from_resolution("p1", "conversation")
    context = product_profile_to_state_context(
        ProductProfile(
            id="p1",
            name="商品",
            summary="说明",
            usage="整段用法",
            updated_at="not-an-iso-time",
        )
    )
    evidence = retrieval_document_to_state_evidence(
        RetrievalDocument(chunk_id="chunk-only", content="证据")
    )

    assert request_reference.source == "customer_selected"
    assert url_reference.source == "message_extraction"
    assert binding_reference.source == "manual"
    assert context.usage == ["整段用法"]
    assert context.source_updated_at is None
    assert evidence.document_id == "chunk-only"
