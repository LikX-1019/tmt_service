"""Unified Chat Legacy Runtime 与 Agent Graph 的 G2A parity 测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agent.dependencies import AgentCapabilities
from app.agent.checkpoint import FileCheckpointStore
from app.agent.chat_runtime import ChatStateRuntime
from app.agent.coordinator import StateCoordinator
from app.agent.graph import build_unified_chat_graph
from app.agent.runtime import AgentRuntime
from app.services.chat_response_mapper import agent_state_to_chat_response
from app.rules.registry import default_rule_registry
from app.services.chat_service import ChatService
from app.services.contextual_fallback_service import ContextualFallbackService
from app.services.product_resolver import ProductResolver
from app.services.product_service import (
    ProductAnswer,
    ProductNotFoundError,
    ProductProfile,
    ProductSearchResult,
)
from app.services.social_router import SocialRouter


@dataclass
class FakeConversationRepository:
    product_id: str | None = None
    product_name: str | None = None
    recent_products: list[dict[str, str]] = field(default_factory=list)
    cleared: bool = False

    async def get_binding(self, conversation_id: str):
        if self.product_id is None:
            return None
        return type(
            "Binding",
            (),
            {
                "product_id": self.product_id,
                "product_name": self.product_name,
                "recent_products": self.recent_products,
            },
        )()

    async def bind_product(
        self,
        conversation_id: str,
        *,
        product_id: str,
        product_name: str,
        recent_products=None,
    ):
        self.product_id = product_id
        self.product_name = product_name
        self.recent_products = list(recent_products or [])

    async def clear_binding(self, conversation_id: str):
        self.cleared = True
        self.product_id = None
        self.product_name = None
        self.recent_products = []


class FakeProductRepository:
    def __init__(self, products: list[ProductProfile], candidates: list[ProductSearchResult]):
        self.products = {item.id: item for item in products}
        self.candidates = candidates

    async def get_product_by_id(self, product_id: str):
        item = self.products.get(product_id)
        if item is None:
            raise ProductNotFoundError(product_id)
        return item

    async def search_products_by_name(self, query: str, *, limit: int = 5):
        return self.candidates[:limit]


class FakeQA:
    def __init__(self, exact=None, documents=None, fail_retrieval=False):
        self.exact = exact
        self.documents = documents or []
        self.fail_retrieval = fail_retrieval
        self.exact_kwargs: dict[str, Any] | None = None

    def match_exact(self, query, **kwargs):
        self.exact_kwargs = kwargs
        return self.exact

    async def retrieve_context_candidates(self, query):
        if self.fail_retrieval:
            raise RuntimeError("rag unavailable")
        return self.documents


class FakeProductAnswers:
    async def answer(self, query, product, *, conversation_id=None):
        return ProductAnswer(
            answer=f"product answer:{product.id}:{query}",
            facts_supported=True,
            contains_sensitive_or_after_sales=False,
            needs_clarification=False,
            confidence=0.97,
        )


class FakeFallback(ContextualFallbackService):
    def __init__(self, answer="fallback answer", needs_human=False, fail=False, confidence=0.5):
        super().__init__(llm=object())
        self.answer = answer
        self.needs_human = needs_human
        self.fail = fail
        self.confidence = confidence
        self.kwargs: dict[str, Any] | None = None

    async def generate(self, query, *, history=(), product=None, references=()):
        if self.fail:
            raise RuntimeError("llm failed")
        self.kwargs = {
            "query": query,
            "history": list(history),
            "product": product,
            "references": list(references),
        }
        from app.services.contextual_fallback_service import ContextualFallbackAnswer

        return ContextualFallbackAnswer(
            answer=self.answer,
            confidence=self.confidence,
            needs_human=self.needs_human,
            reason_code="low_confidence" if self.needs_human else None,
        )


class FixedSemanticResolver:
    def __init__(self, matched=True, ambiguous=False, product_id=None):
        self.matched = matched
        self.ambiguous = ambiguous
        self.product_id = product_id

    async def resolve(self, query, *, candidates, current_product_id=None):
        from app.services.semantic_product_resolver import SemanticProductResolution

        return SemanticProductResolution(
            matched=self.matched,
            product_id=self.product_id,
            confidence=0.95 if self.matched else 0.2,
            ambiguous=self.ambiguous,
            reason_code="ambiguous" if self.ambiguous else "semantic_match",
        )


def product(id="p1", name="Water Cup"):
    return ProductProfile(id=id, name=name, summary=f"{name} summary")


def product_response(index=1):
    return ProductAnswer(
        answer=f"product answer:{index}:question",
        facts_supported=True,
        contains_sensitive_or_after_sales=False,
        needs_clarification=False,
        confidence=0.97,
    )


def graph_state(message, product_id=None):
    from app.agent.state import ProductReference

    state = ChatStateRuntime().create_state(
        message=message,
        conversation_id=f"c-{message}",
        customer_id="u1",
        service_stage="general",
    )
    if product_id is not None:
        state.turn.product.reference = ProductReference(product_id=product_id)
        state.turn.product.resolution_source = "request"
    return state


def capabilities(
    *,
    legacy_repo,
    graph_repo,
    qa=None,
    products=None,
    candidates=None,
    semantic=None,
    answers=None,
    fallbacks=None,
):
    qa = qa or FakeQA()
    product_profile = product()
    product_repo = products or FakeProductRepository(
        [product_profile],
        candidates
        or [
            ProductSearchResult(
                id="p1",
                name="Water Cup",
                summary="cup",
                match_type="exact",
            )
        ],
    )
    answers = answers or FakeProductAnswers()
    fallback = fallbacks or FakeFallback()
    semantic = semantic or FixedSemanticResolver(matched=False)
    def graph_capabilities(conversations):
        return AgentCapabilities(
            rules=default_rule_registry(),
            social_router=SocialRouter(),
            product_resolver=ProductResolver(),
            semantic_products=semantic,
            product_answers=answers,
            fallbacks=fallback,
            qa_provider=lambda: _async(qa),
            conversations=conversations,
            products=product_repo,
        )

    facade_coordinator = StateCoordinator(FileCheckpointStore())
    facade = ChatService(
        agent_runtime=AgentRuntime(
            build_unified_chat_graph(
                graph_capabilities(legacy_repo),
                coordinator=facade_coordinator,
            ),
            coordinator=facade_coordinator,
        )
    )
    direct_caps = AgentCapabilities(
        rules=default_rule_registry(),
        social_router=SocialRouter(),
        product_resolver=ProductResolver(),
        semantic_products=semantic,
        product_answers=answers,
        fallbacks=fallback,
        qa_provider=lambda: _async(qa),
        conversations=graph_repo,
        products=product_repo,
    )
    direct_coordinator = StateCoordinator(FileCheckpointStore())
    return facade, AgentRuntime(
        build_unified_chat_graph(direct_caps, coordinator=direct_coordinator),
        coordinator=direct_coordinator,
    )


async def _async(value):
    return value


async def run_both(message, *, product_id=None, binding=None, **kwargs):
    legacy_repo = FakeConversationRepository(**(binding or {}))
    graph_repo = FakeConversationRepository(**(binding or {}))
    service, runtime = capabilities(
        legacy_repo=legacy_repo,
        graph_repo=graph_repo,
        **kwargs,
    )
    from app.schemas.chat import ChatRequest

    request = ChatRequest(
        conversation_id=f"c-{message}",
        customer_id="u1",
        message=message,
        product_id=product_id,
        service_stage="general",
    )
    legacy_response = await service.chat(request)
    state = graph_state(message, product_id)
    graph_state_result = await runtime.invoke(state)
    graph_response = agent_state_to_chat_response(graph_state_result)
    return legacy_response, graph_response, legacy_repo, graph_repo, graph_state_result


def core_fields(response):
    return {
        "answer": response.answer,
        "source": response.source,
        "route": response.route,
        "product": response.product,
        "products": response.products,
        "product_resolution": response.product_resolution,
        "rule_name": response.rule_name,
        "reason_code": response.reason_code,
        "confidence": response.confidence,
        "qa_hit": response.qa_hit,
        "sources": response.sources,
    }


@pytest.mark.asyncio
async def test_faq_exact_hit_has_legacy_parity():
    from app.qa.models import QAResult

    qa_result = QAResult(answer="faq answer", route="faq", confidence=1.0, sources=[])
    legacy, graph, _, _, graph_result = await run_both(
        "how to use", qa=FakeQA(exact=qa_result)
    )
    assert core_fields(legacy) == core_fields(graph)
    assert graph_result.turn.reply.source == "qa"


@pytest.mark.asyncio
async def test_faq_exact_beats_guard_is_frozen_legacy_compatibility_debt():
    from app.qa.models import QAResult

    qa_result = QAResult(answer="faq refund", route="faq", confidence=1.0)
    legacy, graph, _, _, state = await run_both(
        "我要退款", qa=FakeQA(exact=qa_result)
    )
    assert core_fields(legacy) == core_fields(graph)
    assert state.context["faq_hit"] is True
    assert state.completed_nodes[1] == "faq_exact"
    assert state.completed_nodes[2] == "response"


@pytest.mark.parametrize(
    ("message", "expected_route", "expected_source"),
    [
        ("你好", "greeting", "rule"),
        ("谢谢", "small_talk", "rule"),
        ("我要人工", "human", "rule"),
        ("我要退款", "human", "rule"),
    ],
)
@pytest.mark.asyncio
async def test_rule_paths_have_legacy_parity(message, expected_route, expected_source):
    legacy, graph, _, _, _ = await run_both(message)
    assert core_fields(legacy) == core_fields(graph)
    assert graph.route == expected_route
    assert graph.source == expected_source


@pytest.mark.asyncio
async def test_refund_policy_question_continues_to_fallback():
    legacy, graph, _, _, state = await run_both("支持退款吗？")
    assert core_fields(legacy) == core_fields(graph)
    assert state.completed_nodes[-1] == "response"
    assert "fallback" in state.completed_nodes


@pytest.mark.asyncio
async def test_social_expression_uses_social_node_and_response():
    legacy, graph, _, _, state = await run_both("很开心认识你")
    assert core_fields(legacy) == core_fields(graph)
    assert state.completed_nodes[3] == "social"
    assert state.context["social_hit"] is True


@pytest.mark.asyncio
async def test_explicit_product_request_loads_binds_and_answers():
    legacy, graph, legacy_repo, graph_repo, state = await run_both(
        "question", product_id="p1"
    )
    assert core_fields(legacy) == core_fields(graph)
    assert legacy_repo.product_id == graph_repo.product_id == "p1"
    assert state.turn.product.status == "resolved"
    assert state.turn.product.resolution_source == "request"


@pytest.mark.asyncio
async def test_bound_followup_keeps_mysql_binding_source_of_truth():
    binding = {
        "product_id": "p1",
        "product_name": "Water Cup",
        "recent_products": [{"product_id": "p1", "product_name": "Water Cup"}],
    }
    legacy, graph, legacy_repo, graph_repo, state = await run_both(
        "怎么使用", binding=binding
    )
    assert core_fields(legacy) == core_fields(graph)
    assert legacy_repo.product_id == graph_repo.product_id == "p1"
    assert state.turn.product.context.product_id == "p1"


@pytest.mark.asyncio
async def test_product_name_candidates_do_not_bind():
    candidates = [
        ProductSearchResult(id="p1", name="Cup A", summary="a", match_type="contains"),
        ProductSearchResult(id="p2", name="Cup B", summary="b", match_type="contains"),
    ]
    legacy, graph, legacy_repo, graph_repo, state = await run_both(
        "帮我看看水杯",
        products=FakeProductRepository([], candidates),
        candidates=candidates,
    )
    assert core_fields(legacy) == core_fields(graph)
    assert legacy_repo.product_id is None
    assert graph_repo.product_id is None
    assert state.turn.product.status == "selection_required"
    assert len(graph.products) == 2


@pytest.mark.asyncio
async def test_stale_bound_product_is_cleared():
    binding = {"product_id": "gone", "product_name": "Gone"}
    legacy, graph, legacy_repo, graph_repo, state = await run_both(
        "怎么使用", binding=binding
    )
    assert core_fields(legacy) == core_fields(graph)
    assert legacy_repo.cleared is True
    assert graph_repo.cleared is True
    assert state.turn.product.status == "not_found"
    assert state.session.current_product is None
    assert "gone" not in state.session.recent_product_ids


@pytest.mark.asyncio
async def test_semantic_history_reference_resolves_to_recent_product():
    semantic = FixedSemanticResolver(matched=True, product_id="p2")
    binding = {
        "product_id": "p1",
        "product_name": "Bottle",
        "recent_products": [
            {"product_id": "p1", "product_name": "Bottle"},
            {"product_id": "p2", "product_name": "Cup"},
        ],
    }
    products = FakeProductRepository([product("p1", "Bottle"), product("p2", "Cup")], [])
    legacy, graph, legacy_repo, graph_repo, state = await run_both(
        "刚刚那个呢", binding=binding, products=products, semantic=semantic
    )
    assert core_fields(legacy) == core_fields(graph)
    assert graph_repo.product_id == "p2"
    assert state.turn.product.resolution_source == "history_semantic"


@pytest.mark.asyncio
async def test_fallback_and_bound_context_have_parity():
    fallback = FakeFallback(answer="context fallback", confidence=0.4)
    legacy, graph, _, _, state = await run_both(
        "unknown question",
        binding={"product_id": "p1", "product_name": "Water Cup"},
        fallbacks=fallback,
    )
    assert core_fields(legacy) == core_fields(graph)
    print("REPLY", state.turn.reply)
    assert fallback.kwargs["product"].id == "p1"
    assert state.turn.reply.source == "llm_fallback"


@pytest.mark.asyncio
async def test_fallback_needs_human_keeps_human_contract():
    fallback = FakeFallback(answer="please wait", needs_human=True)
    legacy, graph, _, _, state = await run_both("unknown question", fallbacks=fallback)
    assert core_fields(legacy) == core_fields(graph)
    assert graph.route == "human"
    assert state.session.human.required is True


@pytest.mark.asyncio
async def test_rag_retrieval_failure_does_not_block_fallback():
    fallback = FakeFallback(answer="fallback after rag failure")
    legacy, graph, _, _, state = await run_both(
        "unknown question", qa=FakeQA(fail_retrieval=True), fallbacks=fallback
    )
    assert core_fields(legacy) == core_fields(graph)
    assert state.turn.reply.source == "llm_fallback"
