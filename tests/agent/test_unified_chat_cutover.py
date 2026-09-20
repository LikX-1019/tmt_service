"""G2B Unified Chat 生产切流、回滚开关与 checkpoint 行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.checkpoint import FileCheckpointStore
from app.agent.chat_runtime import ChatStateRuntime
from app.agent.coordinator import StateCoordinator
from app.agent.dependencies import AgentCapabilities
from app.agent.graph import build_unified_chat_graph
from app.agent.protocols import AgentGraphExecutionError
from app.agent.runtime import AgentRuntime
from app.agent.state import AgentReplyState
from app.api.dependencies import get_chat_service
from app.core.config import get_settings
from app.core.exceptions import LLMInvocationError
from app.rules.registry import default_rule_registry
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.contextual_fallback_service import (
    ContextualFallbackAnswer,
    ContextualFallbackService,
)
from app.services.product_resolver import ProductResolver
from app.services.semantic_product_resolver import SemanticProductResolver
from app.services.social_router import SocialRouter
from app.services.product_service import ProductAnswer, ProductProfile


class FakeMessages:
    def __init__(self, history=None):
        self.history = list(history or [])
        self.customer_writes = 0
        self.assistant_writes = 0

    async def list_recent_turns(self, conversation_id: str, *, limit: int = 10):
        return list(self.history)

    async def append_customer_message(self, conversation_id: str, content: str):
        self.customer_writes += 1
        self.history.append({"customer": content, "assistant": None})

    async def append_assistant_message(self, conversation_id: str, content: str):
        self.assistant_writes += 1
        if self.history:
            self.history[-1]["assistant"] = content


class FakeConversations:
    product_id = None
    cleared = False

    async def get_binding(self, conversation_id: str):
        return None

    async def bind_product(self, conversation_id: str, **kwargs):
        self.product_id = kwargs["product_id"]

    async def clear_binding(self, conversation_id: str):
        self.cleared = True


class FakeProducts:
    def __init__(self, product_id="p1"):
        self.product = ProductProfile(
            id=product_id,
            name="Water Cup",
            summary="product summary",
        )

    async def get_product_by_id(self, product_id: str):
        if product_id != self.product.id:
            raise AssertionError(product_id)
        return self.product

    async def search_products_by_name(self, query: str, *, limit: int = 5):
        return []


class FakeAnswers:
    fail = False

    async def answer(self, query, product, *, conversation_id=None):
        if self.fail:
            raise RuntimeError("product llm failed")
        return ProductAnswer(
            answer="product answer",
            facts_supported=True,
            contains_sensitive_or_after_sales=False,
            needs_clarification=False,
            confidence=0.98,
        )


class FakeFallback(ContextualFallbackService):
    def __init__(self):
        super().__init__(llm=object())
        self.history = None

    async def generate(self, query, *, history=(), product=None, references=()):
        self.history = list(history)
        return ContextualFallbackAnswer(
            answer="fallback answer",
            confidence=0.7,
        )


def capabilities(conversations=None, answers=None, fallback=None, messages=None):
    return AgentCapabilities(
        rules=default_rule_registry(),
        social_router=SocialRouter(),
        product_resolver=ProductResolver(),
        semantic_products=SemanticProductResolver(),
        product_answers=answers or FakeAnswers(),
        fallbacks=fallback or FakeFallback(),
        conversations=conversations or FakeConversations(),
        messages=messages,
        products=FakeProducts(),
    )


def make_service(
    *,
    messages=None,
    checkpoint_dir=None,
    mode="graph",
    runtime=None,
    capabilities_instance=None,
):
    store = (
        FileCheckpointStore(checkpoint_dir) if checkpoint_dir else None
    )
    coordinator = StateCoordinator(store) if store is not None else None
    state_runtime = ChatStateRuntime(
        store,
        cleanup_completed=False,
        lifecycle_mode=mode,
    )
    conversations = FakeConversations()
    caps = capabilities_instance or capabilities(conversations=conversations)
    service = ChatService(
        conversation_repository=conversations,
        message_repository=messages,
        state_runtime=state_runtime,
        agent_runtime=runtime
        or AgentRuntime(
            build_unified_chat_graph(caps, coordinator=coordinator),
            coordinator=coordinator,
        ),
        runtime_mode=mode,
    )
    return service, state_runtime, store


def request(message, conversation_id="c1", product_id=None):
    return ChatRequest(
        conversation_id=conversation_id,
        customer_id="u1",
        message=message,
        product_id=product_id,
        service_stage="general",
    )


@pytest.mark.asyncio
async def test_graph_is_default_and_does_not_call_legacy_route(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("graph mode must not call _route_chat")

    monkeypatch.setattr(ChatService, "_route_chat", forbidden)
    service, _, _ = make_service()
    response = await service.chat(request("今天天气怎么样"))

    assert service._runtime_mode == "graph"
    assert response.source == "llm_fallback"
    assert response.answer == "fallback answer"


@pytest.mark.asyncio
async def test_legacy_mode_does_not_invoke_graph():
    class ExplodingRuntime:
        async def invoke(self, state):
            raise AssertionError("legacy mode must not invoke graph")

    service, _, _ = make_service(mode="legacy", runtime=ExplodingRuntime())
    response = await service.chat(request("你好"))

    assert response.route == "greeting"
    assert response.source == "rule"


@pytest.mark.asyncio
async def test_graph_failure_does_not_fall_back_to_legacy(monkeypatch):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("automatic rollback is forbidden")

    monkeypatch.setattr(ChatService, "_route_chat", forbidden)

    class ExplodingRuntime:
        async def invoke(self, state):
            failed = state.model_copy(deep=True)
            failed.begin_node("guard")
            failed.fail_node("guard", code="TEST", retryable=True)
            raise AgentGraphExecutionError(
                "graph failed",
                failed_state=failed,
            )

    service, _, _ = make_service(runtime=ExplodingRuntime())
    with pytest.raises(AgentGraphExecutionError):
        await service.chat(request("今天天气怎么样"))

    assert calls == []


@pytest.mark.asyncio
async def test_customer_and_assistant_are_persisted_exactly_once(tmp_path: Path):
    messages = FakeMessages()
    service, _, store = make_service(messages=messages, checkpoint_dir=tmp_path)
    state = service._state_runtime.create_state(
        message="今天天气怎么样",
        conversation_id="persist-success",
        customer_id="u1",
        service_stage="general",
    )
    service._state_runtime._store = store
    await service.chat(request("今天天气怎么样", conversation_id="persist-success"))

    assert messages.customer_writes == 1
    assert messages.assistant_writes == 1
    saved = await store.load(state.run_id)
    assert saved is None


@pytest.mark.asyncio
async def test_failure_persists_actual_graph_node_and_no_assistant(tmp_path: Path):
    answers = FakeAnswers()
    answers.fail = True
    messages = FakeMessages()
    service, _, store = make_service(
        messages=messages,
        checkpoint_dir=tmp_path,
        capabilities_instance=capabilities(answers=answers),
    )
    request_data = request("question", conversation_id="persist-failure", product_id="p1")
    with pytest.raises(LLMInvocationError):
        await service.chat(request_data)

    assert messages.customer_writes == 1
    assert messages.assistant_writes == 0
    checkpoint_files = list(tmp_path.glob("*.json"))
    assert len(checkpoint_files) == 1
    saved = await store.load(checkpoint_files[0].stem)
    assert saved is not None
    assert saved.status.value == "failed"
    assert saved.error.node == "product_answer"
    assert saved.resume_from == "product_answer"


@pytest.mark.asyncio
async def test_hydration_excludes_current_customer_from_fallback_history():
    messages = FakeMessages([{"customer": "old question", "assistant": "old answer"}])
    fallback = FakeFallback()
    service, _, _ = make_service(
        messages=messages,
        capabilities_instance=capabilities(fallback=fallback, messages=messages),
    )

    await service.chat(request("current question", conversation_id="history"))

    print("DEBUG_HISTORY", fallback.history)
    print("DEBUG_MEMORY", None)
    assert fallback.history == [{"customer": "old question", "assistant": "old answer"}]
    assert messages.customer_writes == 1


@pytest.mark.asyncio
async def test_generated_conversation_id_is_shared(tmp_path: Path):
    messages = FakeMessages()
    service, state_runtime, store = make_service(
        messages=messages,
        checkpoint_dir=tmp_path,
    )
    original_create = state_runtime.create_state
    created = []

    def capture_state(**kwargs):
        state = original_create(**kwargs)
        created.append(state)
        return state

    state_runtime.create_state = capture_state
    response = await service.chat(request("今天天气怎么样", conversation_id=None))

    assert response.conversation_id
    assert messages.history
    assert created and created[0].conversation_id == response.conversation_id


@pytest.mark.asyncio
async def test_explicit_product_request_is_adapted_before_graph():
    service, _, _ = make_service()
    response = await service.chat(request("question", product_id="p1"))

    assert response.source == "product"
    assert response.product_resolution == "request"
    assert response.product is not None and response.product.id == "p1"


@pytest.mark.asyncio
async def test_graph_success_checkpoint_preserves_node_trace(tmp_path: Path):
    messages = FakeMessages()
    service, state_runtime, store = make_service(
        messages=messages,
        checkpoint_dir=tmp_path,
    )
    request_data = request("今天天气怎么样", conversation_id="checkpoint-success")
    await service.chat(request_data)

    checkpoint_files = list(tmp_path.glob("*.json"))
    assert len(checkpoint_files) == 1
    saved = await store.load(checkpoint_files[0].stem)
    assert saved.status.value == "completed"
    assert saved.error is None
    assert saved.current_node is None
    assert "faq_exact" in saved.completed_nodes
    assert "response" in saved.completed_nodes


@pytest.mark.asyncio
async def test_production_dependency_composes_graph_mode(monkeypatch):
    async def no_binding(self, conversation_id):
        return None

    async def append_customer(self, conversation_id, content):
        return None

    async def append_assistant(self, conversation_id, content):
        return None

    monkeypatch.setattr(
        "app.repositories.conversation_repository.ConversationProductRepository.get_binding",
        no_binding,
    )
    monkeypatch.setattr(
        "app.repositories.chat_message_repository.ChatConversationMessageRepository.append_customer_message",
        append_customer,
    )
    monkeypatch.setattr(
        "app.repositories.chat_message_repository.ChatConversationMessageRepository.append_assistant_message",
        append_assistant,
    )
    async def fake_invoke(self, state):
        from app.agent.state import StatePatch

        state.apply_patch(
            StatePatch(
                reply=AgentReplyState(
                    source="rule",
                    route="greeting",
                    answer="graph called",
                    rule_name="GraphProof",
                ),
                final_answer="graph called",
            )
        )
        return state

    monkeypatch.setattr(AgentRuntime, "invoke", fake_invoke)
    service = get_chat_service.__wrapped__()
    assert service._runtime_mode == "graph"
    assert service._agent_runtime is not None
    response = await service.chat(request("今天天气怎么样", conversation_id="di"))

    assert response.answer == "graph called"
    assert get_settings().unified_chat_runtime == "graph"
