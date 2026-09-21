"""G3 durable Node checkpoint、revision、resume 与 PendingAction safety 测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agent.checkpoint import (
    CheckpointConflictError,
    FileCheckpointStore,
)
from app.agent.chat_runtime import ChatStateRuntime
from app.agent.coordinator import StateCoordinator
from app.agent.dependencies import AgentCapabilities
from app.agent.graph import build_unified_chat_graph
from app.agent.runtime import AgentRuntime
from app.agent.state import (
    ActionStatus,
    AgentReplyState,
    AgentState,
    NodeTrace,
    PendingAction,
    ProductReference,
    StatePatch,
    WorkflowStatus,
)
from app.qa.models import QAResult
from app.agent.state_mappers import rule_result_to_patch
from app.rules.registry import default_rule_registry
from app.services.contextual_fallback_service import (
    ContextualFallbackAnswer,
    ContextualFallbackService,
)
from app.services.product_resolver import ProductResolver
from app.services.product_service import (
    ProductAnswer,
    ProductNotFoundError,
    ProductProfile,
    ProductSearchResult,
)
from app.services.semantic_product_resolver import SemanticProductResolver
from app.services.social_router import SocialRouter


class RecordingCheckpointStore(FileCheckpointStore):
    def __init__(self, directory):
        super().__init__(directory)
        self.reasons: list[str] = []

    async def save(self, state: AgentState, *, reason: str) -> AgentState:
        self.reasons.append(reason)
        return await super().save(state, reason=reason)


@dataclass
class FakeConversations:
    bind_calls: int = 0
    clear_calls: int = 0

    async def get_binding(self, conversation_id: str):
        return None

    async def bind_product(self, conversation_id: str, **kwargs: Any):
        self.bind_calls += 1

    async def clear_binding(self, conversation_id: str):
        self.clear_calls += 1


@dataclass
class FakeProductRepository:
    profiles: dict[str, ProductProfile]
    candidates: list[ProductSearchResult] = field(default_factory=list)
    load_calls: int = 0
    search_calls: int = 0
    bind_calls: int = 0

    async def get_product_by_id(self, product_id: str) -> ProductProfile:
        self.load_calls += 1
        profile = self.profiles.get(product_id)
        if profile is None:
            raise ProductNotFoundError(product_id)
        return profile

    async def search_products_by_name(self, query: str, *, limit: int = 5):
        self.search_calls += 1
        return self.candidates[:limit]

    async def bind_product(self, conversation_id: str, **kwargs: Any) -> None:
        self.bind_calls += 1


class FakeQA:
    def __init__(self, exact: QAResult | None = None):
        self.exact = exact

    def match_exact(self, query: str, **kwargs: Any) -> QAResult | None:
        return self.exact

    async def retrieve_context_candidates(self, query: str):
        return []


@dataclass
class FakeAnswers:
    fail: bool = False
    calls: int = 0

    async def answer(self, query: str, product: ProductProfile, *, conversation_id=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("product answer failed")
        return ProductAnswer(
            answer="product answer",
            facts_supported=True,
            contains_sensitive_or_after_sales=False,
            needs_clarification=False,
            confidence=0.97,
        )


class FakeFallback(ContextualFallbackService):
    def __init__(self):
        super().__init__(llm=object())
        self.calls = 0

    async def generate(self, query: str, *, history=(), product=None, references=()):
        self.calls += 1
        return ContextualFallbackAnswer(
            answer="fallback answer",
            confidence=0.8,
        )


@dataclass
class Fixture:
    store: RecordingCheckpointStore
    runtime: AgentRuntime
    answers: FakeAnswers
    products: FakeProductRepository
    conversations: FakeConversations
    fallback: FakeFallback


def make_fixture(tmp_path, *, fail_first=False, faq_exact: QAResult | None = None) -> Fixture:
    store = RecordingCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    answers = FakeAnswers(fail=fail_first)
    products = FakeProductRepository(
        {"p1": ProductProfile(id="p1", name="Water Cup", summary="A cup")}
    )
    fallback = FakeFallback()
    conversations = FakeConversations()
    capabilities = AgentCapabilities(
        rules=default_rule_registry(),
        social_router=SocialRouter(),
        product_resolver=ProductResolver(),
        semantic_products=SemanticProductResolver(),
        product_answers=answers,
        fallbacks=fallback,
        qa_provider=_fake_qa_provider(FakeQA(faq_exact)),
        conversations=conversations,
        products=products,
    )
    graph = build_unified_chat_graph(capabilities, coordinator=coordinator)
    runtime = AgentRuntime(graph, coordinator=coordinator)
    return Fixture(store, runtime, answers, products, conversations, fallback)


def _fake_qa_provider(qa: FakeQA):
    async def provider():
        return qa

    return provider


def state(message: str, product_id: str | None = None) -> AgentState:
    runtime = ChatStateRuntime()
    result = runtime.create_state(
        message=message,
        conversation_id=f"c-{message}",
        customer_id="u1",
        service_stage="general",
    )
    if product_id is not None:
        result.turn.product.reference = ProductReference(product_id=product_id)
        result.turn.product.resolution_source = "request"
    return result


def test_successful_greeting_persist_exact_reason_sequence(tmp_path):
    fixture = make_fixture(tmp_path)
    result = asyncio_run(fixture.runtime.invoke(state("你好")))

    assert result.status is WorkflowStatus.COMPLETED
    assert result.completed_nodes == [
        "session_hydrate",
        "faq_exact",
        "guard",
        "response",
    ]
    assert fixture.store.reasons == [
        "workflow_created",
        "before:session_hydrate",
        "after:session_hydrate",
        "before:faq_exact",
        "after:faq_exact",
        "before:guard",
        "after:guard",
        "before:response",
        "after:response",
    ]


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_successful_product_path_has_before_and_after_for_every_node(tmp_path):
    fixture = make_fixture(tmp_path)
    result = asyncio_run(fixture.runtime.invoke(state("question", "p1")))

    expected = [
        "session_hydrate",
        "faq_exact",
        "guard",
        "social",
        "product_resolve",
        "product_load",
        "product_answer",
        "response",
    ]
    assert result.completed_nodes == expected
    for node in expected:
        assert f"before:{node}" in fixture.store.reasons
        assert f"after:{node}" in fixture.store.reasons
    assert result.revision >= len(fixture.store.reasons)


def test_faq_exact_early_return_does_not_checkpoint_later_nodes(tmp_path):
    exact = QAResult(answer="faq answer", route="faq", confidence=1.0)
    fixture = make_fixture(tmp_path, faq_exact=exact)
    result = asyncio_run(fixture.runtime.invoke(state("faq question")))

    assert result.completed_nodes == [
        "session_hydrate",
        "faq_exact",
        "response",
    ]
    assert "before:social" not in fixture.store.reasons
    assert "before:product_resolve" not in fixture.store.reasons


def test_failed_product_answer_persists_before_and_failed_without_after(tmp_path):
    fixture = make_fixture(tmp_path, fail_first=True)
    input_state = state("question", "p1")
    run_id = input_state.run_id

    with pytest.raises(Exception) as exc_info:
        asyncio_run(fixture.runtime.invoke(input_state))

    assert "before:product_answer" in fixture.store.reasons
    assert "failed:product_answer" in fixture.store.reasons
    assert "after:product_answer" not in fixture.store.reasons
    saved = asyncio_run(await_store_load(fixture.store, run_id))
    assert saved.status is WorkflowStatus.FAILED
    assert saved.error.node == "product_answer"
    assert saved.resume_from == "product_answer"
    assert exc_info.value.failed_state.error.node == "product_answer"


async def await_store_load(store, run_id):
    return await store.load(run_id)


def test_failed_product_answer_resume_retries_only_failed_node(tmp_path):
    fixture = make_fixture(tmp_path, fail_first=True)
    input_state = state("question", "p1")
    run_id = input_state.run_id
    with pytest.raises(Exception):
        asyncio_run(fixture.runtime.invoke(input_state))

    fixture.answers.fail = False
    resumed = asyncio_run(fixture.runtime.resume(run_id))

    assert resumed.status is WorkflowStatus.COMPLETED
    assert fixture.answers.calls == 2
    assert fixture.products.load_calls == 1
    assert fixture.conversations.bind_calls == 1
    assert fixture.fallback.calls == 0
    answer_traces = [
        trace for trace in resumed.node_trace if trace.node == "product_answer"
    ]
    assert [trace.attempt for trace in answer_traces] == [1, 2]
    assert answer_traces[0].status.value == "failed"
    assert answer_traces[1].status.value == "completed"


def test_interrupted_fallback_resume_starts_at_fallback_attempt_two(tmp_path):
    fixture = make_fixture(tmp_path)
    input_state = state("unknown topic")
    run_id = input_state.run_id
    asyncio_run(fixture.runtime.invoke(input_state))

    # Simulate a crash after before:fallback had been persisted.
    interrupted = asyncio_run(await_store_load(fixture.store, run_id))
    interrupted.status = WorkflowStatus.RUNNING
    interrupted.current_node = "fallback"
    interrupted.resume_from = "fallback"
    interrupted.checkpoint_reason = "before:fallback"
    interrupted.completed_nodes = [
        node for node in interrupted.completed_nodes if node not in {"fallback", "response"}
    ]
    interrupted.node_trace = [
        trace for trace in interrupted.node_trace if trace.node != "fallback"
    ]
    interrupted.node_trace.append(NodeTrace(node="fallback", attempt=1))
    asyncio_run(fixture.store.save(interrupted, reason="before:fallback"))
    fallback_calls = fixture.fallback.calls
    product_loads = fixture.products.load_calls

    resumed = asyncio_run(fixture.runtime.resume(run_id))

    assert resumed.status is WorkflowStatus.COMPLETED
    assert fixture.fallback.calls == fallback_calls + 1
    assert fixture.products.load_calls == product_loads
    fallback_traces = [
        trace for trace in resumed.node_trace if trace.node == "fallback"
    ]
    assert [trace.attempt for trace in fallback_traces] == [1, 2]


@pytest.mark.parametrize("status", [ActionStatus.EXECUTING, ActionStatus.UNCERTAIN])
def test_pending_action_executing_or_uncertain_is_manual_and_not_resumed(
    tmp_path, status
):
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    runtime = AgentRuntime(
        build_unified_chat_graph(
            AgentCapabilities(
                rules=default_rule_registry(),
                social_router=SocialRouter(),
                product_resolver=ProductResolver(),
                semantic_products=SemanticProductResolver(),
                product_answers=FakeAnswers(),
                fallbacks=FakeFallback(),
            )
        ),
        coordinator=coordinator,
    )
    initial = state("question")
    initial.pending_action = PendingAction(
        action_type="external_send",
        idempotency_key="key-1",
        status=status,
    )
    saved = asyncio_run(coordinator.create(initial))

    resumed = asyncio_run(runtime.resume(saved.run_id))

    assert resumed.status is WorkflowStatus.WAITING_MANUAL
    assert resumed.pending_action.status is ActionStatus.UNCERTAIN
    assert resumed.resume_from is None


def test_inconsistent_succeeded_pending_action_is_manual(tmp_path):
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    initial = state("question")
    initial.pending_action = PendingAction(
        action_type="external_send",
        idempotency_key="key-2",
        status=ActionStatus.SUCCEEDED,
    )
    initial.current_node = "product_answer"
    initial.status = WorkflowStatus.RUNNING
    saved = asyncio_run(coordinator.create(initial))

    resumed = asyncio_run(StateCoordinator(store).load_for_resume(saved.run_id))

    assert resumed.status is WorkflowStatus.WAITING_MANUAL
    assert resumed.resume_from is None


def test_checkpoint_conflict_is_not_overwritten(tmp_path):
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    initial = state("question")
    created = asyncio_run(coordinator.create(initial))
    stale = created.model_copy(deep=True)
    newer = created.model_copy(deep=True)
    newer.begin_node("faq_exact")
    asyncio_run(store.save(newer, reason="before:faq_exact"))

    async def execute():
        stale.next_node = "faq_exact"
        return await coordinator.run_node(stale, "faq_exact", fake_node)

    async def fake_node(state: AgentState) -> StatePatch:
        return StatePatch(context={"noop": True}, next_node="guard")

    import asyncio

    with pytest.raises(CheckpointConflictError):
        asyncio.run(execute())


def test_legacy_chat_turn_checkpoint_is_manual_not_reexecuted(tmp_path):
    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    initial = state("question")
    initial.next_node = "chat_turn"
    initial.status = WorkflowStatus.RUNNING
    initial.current_node = "chat_turn"
    initial.resume_from = "chat_turn"
    saved = asyncio_run(coordinator.create(initial))

    resumed = asyncio_run(coordinator.load_for_resume(saved.run_id))

    assert resumed.status is WorkflowStatus.WAITING_MANUAL
    assert resumed.next_node == "chat_turn"
    assert resumed.resume_from is None


def test_after_checkpoint_state_round_trips_json(tmp_path):
    fixture = make_fixture(tmp_path)
    result = asyncio_run(fixture.runtime.invoke(state("你好")))
    result.model_dump_json()

    assert AgentState.model_validate_json(result.model_dump_json()).run_id == result.run_id


@pytest.mark.parametrize(
    ("hit", "expected"),
    [(True, "response"), (False, "guard")],
)
def test_faq_next_node_matches_conditional_edge(hit, expected):
    from app.agent.edges.faq_edge import after_faq

    state_obj = state("faq question")
    state_obj.apply_patch(
        StatePatch(
            reply=None
            if not hit
            else AgentReplyState(source="qa", route="faq", answer="answer"),
            next_node=expected,
        )
    )
    assert after_faq(state_obj) == expected


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [(True, "response"), (False, "social")],
)
def test_guard_next_node_matches_conditional_edge(terminal, expected):
    from app.agent.edges.guard_edge import after_guard
    from app.rules.base import RuleContext, RuleDecision
    from app.rules.registry import RuleRegistry

    rule = RuleDecision(
        matched=terminal,
        rule_name="TerminalRule" if terminal else None,
        terminal=terminal,
        route="human" if terminal else None,
        fixed_reply="stop" if terminal else None,
    )

    class FixedRule:
        name = "FixedRule"
        priority = 100
        terminal = bool(rule.terminal)

        def evaluate(self, message, context):
            return rule

    result = RuleRegistry([FixedRule()]).evaluate("message", RuleContext())
    patch = rule_result_to_patch(result)
    state_obj = state("message")
    state_obj.apply_patch(patch)

    assert patch.next_node == ("response" if terminal else "social")
    assert after_guard(state_obj) == expected


@pytest.mark.parametrize(
    ("hit", "expected"),
    [(True, "response"), (False, "product_resolve")],
)
def test_social_next_node_matches_conditional_edge(hit, expected):
    from app.agent.edges.social_edge import after_social

    state_obj = state("social message")
    if hit:
        state_obj.apply_patch(
            StatePatch(
                reply=AgentReplyState(
                    source="rule", route="small_talk", answer="hello"
                ),
                next_node=expected,
            )
        )
    else:
        state_obj.apply_patch(StatePatch(next_node=expected))
    assert after_social(state_obj) == expected


@pytest.mark.parametrize(
    ("answer_required", "expected"),
    [(True, "product_answer"), (False, "fallback")],
)
def test_product_load_next_node_matches_conditional_edge(answer_required, expected):
    from app.agent.edges.product_edge import after_product_load

    state_obj = state("product question")
    state_obj.turn.product.status = "resolved"
    state_obj.turn.product.answer_required = answer_required
    assert after_product_load(state_obj) == expected
