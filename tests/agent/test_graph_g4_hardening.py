"""G4 Graph hardening：topology、RAG/Fallback/Human 分离与 typed routing。"""

from __future__ import annotations

import asyncio

import pytest
from typing import Any

from app.agent.constants import RESUMABLE_GRAPH_NODES
from app.agent.dependencies import AgentCapabilities
from app.agent.graph import build_unified_chat_graph
from app.agent.nodes.rag import RAGContextNode
from app.agent.protocols import AgentNodeExecutionError
from app.agent.state import AgentState
from app.qa.models import RetrievalDocument
from app.rules.registry import default_rule_registry
from app.services.contextual_fallback_service import ContextualFallbackService
from app.services.contextual_fallback_service import ContextualFallbackAnswer
from app.services.product_resolver import ProductResolver
from app.services.product_service import ProductAnswer, ProductProfile
from app.services.semantic_product_resolver import SemanticProductResolver
from app.services.social_router import SocialRouter


class RAGCounter:
    def __init__(self, documents=None, fail=False):
        self.documents = documents or []
        self.fail = fail
        self.calls = 0

    async def retrieve_context_candidates(self, query: str):
        self.calls += 1
        if self.fail:
            raise RuntimeError("rag unavailable")
        return self.documents

    def match_exact(self, query: str, **kwargs: Any):
        return None


def make_capabilities(rag: RAGCounter, answers=None, fallback=None, products=None):
    return AgentCapabilities(
        rules=default_rule_registry(),
        social_router=SocialRouter(),
        product_resolver=ProductResolver(),
        semantic_products=SemanticProductResolver(),
        product_answers=answers,
        fallbacks=fallback or ContextualFallbackService(llm=object()),
        qa_provider=make_qa_provider(rag),
        products=products,
    )


def make_qa_provider(qa: RAGCounter):
    async def provider():
        return qa

    return provider


class FailingFallback(ContextualFallbackService):
    def __init__(self):
        super().__init__(llm=object())
        self.calls = 0

    async def generate(self, query, *, history=(), product=None, references=()):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("fallback llm failed")
        return ContextualFallbackAnswer(answer="recovered", confidence=0.9)


class FailingAnswers:
    def __init__(self):
        self.calls = 0
        self.fail = True

    async def answer(self, query, product, *, conversation_id=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("product llm failed")
        return ProductAnswer(
            answer="answer",
            facts_supported=True,
            contains_sensitive_or_after_sales=False,
            needs_clarification=False,
            confidence=0.9,
        )


def graph_state(message: str) -> AgentState:
    from app.agent.chat_runtime import ChatStateRuntime

    return ChatStateRuntime().create_state(
        message=message,
        conversation_id=f"c-{message}",
        customer_id="u1",
        service_stage="general",
    )


def test_topology_contains_all_active_nodes_and_resume_allowlist_matches():
    graph = build_unified_chat_graph().get_graph()
    expected = {
        "__start__",
        "session_hydrate",
        "faq_exact",
        "guard",
        "social",
        "product_resolve",
        "product_load",
        "product_answer",
        "rag_context",
        "fallback",
        "human_transfer",
        "response",
        "pdd_handoff",
        "pdd_greeting",
        "pdd_intent",
        "pdd_rag",
        "__end__",
    }
    assert expected == set(graph.nodes)
    assert RESUMABLE_GRAPH_NODES == expected - {"__start__", "__end__"}


def test_document_to_evidence_and_back_preserves_fallback_fields():
    document = RetrievalDocument(
        chunk_id="qa-1",
        title="Usage Guide",
        content="Use water only.",
        source="cs_qa",
        metadata={"document_id": "doc-1"},
        rerank_score=0.9,
    )
    capabilities = make_capabilities(RAGCounter([document]))
    node = RAGContextNode(capabilities)
    state = graph_state("how to use")

    patch = asyncio.run(node(state))

    assert patch.retrieval is not None and patch.retrieval.status == "available"
    assert patch.next_node == "fallback"
    evidence = patch.retrieval.candidates[0]
    assert evidence.title == "Usage Guide"
    assert evidence.content == "Use water only."

    state.apply_patch(patch)
    documents = evidence_to_documents(state)
    assert documents[0].title == "Usage Guide"
    assert documents[0].content == "Use water only."


def evidence_to_documents(state: AgentState):
    from app.qa.models import RetrievalDocument

    assert state.turn is not None and state.turn.retrieval is not None
    return [
        RetrievalDocument(
            chunk_id=item.chunk_id or item.document_id,
            content=item.content,
            title=item.title,
            source=item.source,
            metadata=item.metadata,
            dense_score=item.dense_score,
            bm25_score=item.bm25_score,
            fusion_score=item.fusion_score,
            rerank_score=item.rerank_score,
        )
        for item in state.turn.retrieval.candidates
    ]


@pytest.mark.asyncio
async def test_fallback_failure_resume_does_not_repeat_rag(tmp_path):
    from app.agent.checkpoint import FileCheckpointStore
    from app.agent.coordinator import StateCoordinator
    from app.agent.runtime import AgentRuntime
    from app.agent.chat_runtime import ChatStateRuntime

    rag = RAGCounter()
    answers = FailingAnswers()

    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    fallback = FailingFallback()
    graph = build_unified_chat_graph(
        make_capabilities(rag, answers=answers, fallback=fallback),
        coordinator=coordinator,
    )
    runtime = AgentRuntime(graph, coordinator=coordinator)
    initial = ChatStateRuntime().create_state(
        message="unknown question",
        conversation_id="resume",
        customer_id="u1",
        service_stage="general",
    )
    with pytest.raises(AgentNodeExecutionError):
        await runtime.invoke(initial)

    saved = await store.load(initial.run_id)
    assert saved is not None
    assert saved.next_node == "fallback"
    assert rag.calls == 1

    answers.fail = False
    resumed = await runtime.resume(initial.run_id)

    assert resumed.status.value == "completed"
    assert rag.calls == 1
    assert fallback.calls == 2
    assert resumed.node_trace[-2].node == "fallback"
    assert resumed.node_trace[-2].attempt == 2
    assert resumed.final_answer == "recovered"


@pytest.mark.asyncio
async def test_human_transfer_is_explicit_on_fallback_human_path(tmp_path):
    from app.agent.checkpoint import FileCheckpointStore
    from app.agent.coordinator import StateCoordinator
    from app.agent.runtime import AgentRuntime
    from app.services.contextual_fallback_service import ContextualFallbackAnswer

    class HumanFallback(ContextualFallbackService):
        async def generate(self, query, *, history=(), product=None, references=()):
            return ContextualFallbackAnswer(
                answer="Please wait for a human.",
                confidence=0.3,
                needs_human=True,
                reason_code="low_confidence",
            )

    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    graph = build_unified_chat_graph(
        make_capabilities(
            RAGCounter(), fallback=HumanFallback(llm=object())
        ),
        coordinator=coordinator,
    )
    runtime = AgentRuntime(graph, coordinator=coordinator)
    result = await runtime.invoke(graph_state("unknown question"))

    assert "human_transfer" in result.completed_nodes
    assert result.route == "human"
    assert result.turn.reply.source == "llm_fallback"
    assert result.turn.reply.route == "human"
    assert result.session.human.required is True


@pytest.mark.asyncio
async def test_product_missing_returns_contract_without_exception(tmp_path):
    from app.agent.checkpoint import FileCheckpointStore
    from app.agent.coordinator import StateCoordinator
    from app.agent.runtime import AgentRuntime
    from app.agent.chat_runtime import ChatStateRuntime
    class EmptyProducts:
        async def get_product_by_id(self, product_id):
            raise AssertionError(product_id)

        async def search_products_by_name(self, query, *, limit=5):
            return []

    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    graph = build_unified_chat_graph(
        make_capabilities(RAGCounter(), answers=None, products=EmptyProducts())
    )
    runtime = AgentRuntime(graph, coordinator=coordinator)
    state = ChatStateRuntime().create_state(
        message="这个怎么用",
        conversation_id="missing",
        customer_id="u1",
        service_stage="general",
    )
    state.turn.product.requires_product = True
    state.turn.product.product_rule_name = "ProductContextRule"

    result = await runtime.invoke(state)

    assert result.status.value == "completed"
    assert result.route == "product_missing"
    assert result.turn.reply.source == "product"
    assert result.final_answer == "当前还没有识别到您咨询的具体商品，请提供商品信息或商品 ID。"
    assert result.completed_nodes[-2:] == ["product_resolve", "response"]


def test_product_missing_patch_is_strongly_typed_and_rule_name_is_preserved():
    from app.agent.nodes.product import ProductResolveNode

    class Resolver:
        def resolve(self, **kwargs):
            from app.services.product_resolver import ProductResolution

            return ProductResolution(None, "none")

        def extract_name_query(self, message, *, product_intent):
            return None

        def has_url(self, message):
            return False

    capabilities = make_capabilities(RAGCounter(), answers=None)
    node = ProductResolveNode(capabilities)
    state_obj = graph_state("这个怎么用")
    state_obj.turn.product.requires_product = True
    state_obj.turn.product.product_rule_name = "ProductContextRule"

    patch = asyncio.run(node(state_obj))

    assert patch.next_node == "response"
    assert patch.reply is not None
    assert patch.reply.route == "product_missing"
    assert patch.reply.rule_name == "ProductContextRule"
    assert patch.product is not None
    assert patch.product.status == "missing"
    assert patch.product.requires_product is True
    assert patch.product.product_rule_name == "ProductContextRule"


@pytest.mark.asyncio
async def test_core_product_routing_works_without_magic_context_keys(tmp_path):
    from app.agent.checkpoint import FileCheckpointStore
    from app.agent.coordinator import StateCoordinator
    from app.agent.runtime import AgentRuntime
    from app.agent.chat_runtime import ChatStateRuntime
    from app.agent.state import ProductReference

    class Products:
        async def get_product_by_id(self, product_id):
            return ProductProfile(id=product_id, name="Water Cup", summary="cup")

        async def search_products_by_name(self, query, *, limit=5):
            return []

    class Answers:
        async def answer(self, query, product, *, conversation_id=None):
            return ProductAnswer(
                answer="typed routing answer",
                facts_supported=True,
                contains_sensitive_or_after_sales=False,
                needs_clarification=False,
                confidence=0.98,
            )

    store = FileCheckpointStore(tmp_path)
    coordinator = StateCoordinator(store)
    graph = build_unified_chat_graph(
        make_capabilities(RAGCounter(), answers=Answers(), products=Products())
    )
    runtime = AgentRuntime(graph, coordinator=coordinator)
    state = ChatStateRuntime().create_state(
        message="question",
        conversation_id="typed-product",
        customer_id="u1",
        service_stage="general",
    )
    state.turn.product.requires_product = True
    state.turn.product.reference = ProductReference(product_id="p1")
    state.turn.product.resolution_source = "request"

    result = await runtime.invoke(state)

    assert result.context.get("product_answer_required") is None
    assert result.turn.product.answer_required is True
    assert result.final_answer == "typed routing answer"
