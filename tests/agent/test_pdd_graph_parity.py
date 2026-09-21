"""G5A：冻结 PDD Legacy 与共享 AgentGraph 的主要决策行为。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.channels.pdd_adapter import (
    PDDChannelDecision,
    evaluate_pdd_channel_policy,
    graph_state_to_pdd_decision,
    pdd_context_to_agent_state,
    pdd_messages_to_agent_state,
)
from app.agent.checkpoint import FileCheckpointStore
from app.agent.coordinator import StateCoordinator
from app.agent.dependencies import AgentCapabilities
from app.agent.graph import build_unified_chat_graph
from app.agent.routing import CustomerServiceRouter, RouteClassification
from app.agent.runtime import AgentRuntime
from app.agent.service import AgentReply
from app.core.config import Settings
from app.integrations.pdd.base import (
    BrowserMessage,
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerServiceConnector,
    SendReceipt,
)
from app.models import Base, Conversation
from app.qa.models import QAResult, RetrievalDocument
from app.repositories.console_repository import ConsoleRepository
from app.rules.registry import default_rule_registry
from app.services.auto_reply_policy import AutoReplyPolicy, Calibration
from app.services.console_runtime import ConsoleRuntime
from app.services.contextual_fallback_service import ContextualFallbackService
from app.services.product_resolver import ProductResolver
from app.services.product_service import (
    ProductAnswer,
    ProductLookupError,
    ProductProfile,
)
from app.services.semantic_product_resolver import SemanticProductResolver
from app.services.social_router import SocialDecision


class FakeConnector(CustomerServiceConnector):
    """隔离浏览器；Legacy 可验证排队结果，但绝不访问真实 PDD。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def start(self, on_message, on_status, on_profile=None):
        return await self.status()

    async def stop(self):
        return ConnectorStatusSnapshot(ConnectorStatus.STOPPED)

    async def status(self):
        return ConnectorStatusSnapshot(ConnectorStatus.READY)

    async def send_message(self, platform_conversation_id, content):
        self.sent.append(content)
        now = datetime.now(timezone.utc)
        return SendReceipt(clicked_at=now, confirmed_at=now)


class FakeQA:
    def __init__(
        self,
        *,
        exact: QAResult | None = None,
        rag: QAResult | None = None,
        fail: bool = False,
    ) -> None:
        self.exact = exact
        self.rag = rag or QAResult(answer="知识证据不足", route="fallback")
        self.fail = fail

    def match_exact(self, query, **kwargs):
        if self.fail:
            raise RuntimeError("qa unavailable")
        return self.exact

    async def answer_rag(self, query, **kwargs):
        if self.fail:
            raise RuntimeError("qa unavailable")
        return self.rag


class FakeProducts:
    def __init__(self, product: ProductProfile | None = None) -> None:
        self.product = product

    async def get_product(self, product_id: str) -> ProductProfile:
        return await self.get_product_by_id(product_id)

    async def get_product_by_id(self, product_id: str) -> ProductProfile:
        if self.product is None or self.product.id != product_id:
            raise ProductLookupError(product_id)
        return self.product

    async def search_products_by_name(self, query: str, *, limit: int = 5):
        return []


class FakeProductAnswers:
    def __init__(
        self,
        result: ProductAnswer | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.result = result
        self.fail = fail

    async def answer(self, query, product, *, conversation_id=None):
        if self.fail or self.result is None:
            raise RuntimeError("product answer unavailable")
        return self.result

    @staticmethod
    def can_auto_send(answer, *, allow_auto, settings):
        return bool(
            allow_auto
            and answer.facts_supported
            and not answer.contains_sensitive_or_after_sales
            and not answer.needs_clarification
            and answer.confidence >= settings.product_auto_reply_min_confidence
        )


class FakeGreetingAgent:
    def __init__(self, reply: AgentReply | None = None) -> None:
        self.reply = reply or AgentReply(intent="other", confidence=1.0)

    async def run(self, message, customer, *, greeting_config=None):
        return self.reply


class FixedSocialRouter:
    def __init__(self, decision: SocialDecision | None = None) -> None:
        self.decision = decision or SocialDecision()

    async def classify(self, message):
        return self.decision


class FixedPDDRouter(CustomerServiceRouter):
    def __init__(self, route: str = "rag") -> None:
        self.route = route

    async def classify(self, query, **kwargs):
        return RouteClassification(route=self.route, confidence=1.0)


class ForbiddenUnifiedConversationStore:
    """PDD 商品上下文不得读写 Unified Chat 的商品绑定表。"""

    async def get_binding(self, conversation_id):
        raise AssertionError("PDD must not read chat_conversation_products")

    async def bind_product(self, conversation_id, **kwargs):
        raise AssertionError("PDD must not write chat_conversation_products")

    async def clear_binding(self, conversation_id):
        raise AssertionError("PDD must not clear chat_conversation_products")


@dataclass(slots=True)
class ParityResult:
    legacy: dict
    graph: PDDChannelDecision
    state: object
    connector: FakeConnector
    graph_sent_count: int


def message(content: str, *, goods_id: str | None, goods_name: str | None):
    return BrowserMessage(
        platform_conversation_id="buyer-1",
        platform_customer_id="buyer-1",
        display_name="顾客",
        direction="inbound",
        kind="text",
        content=content,
        occurred_at=datetime.now(timezone.utc),
        goods_id=goods_id,
        goods_name=goods_name,
        fingerprint=uuid4().hex * 2,
    )


def faq_result(answer: str = "FAQ 标准答案") -> QAResult:
    return _qa_result("faq", answer, score=1.0, chunk_id="faq-1")


def rag_result(answer: str = "RAG 标准答案", *, score: float = 0.95) -> QAResult:
    return _qa_result("rag", answer, score=score, chunk_id="rag-1")


def fallback_result(answer: str = "知识证据不足") -> QAResult:
    return QAResult(answer=answer, route="fallback", confidence=None)


def _qa_result(route: str, answer: str, *, score: float, chunk_id: str) -> QAResult:
    return QAResult(
        answer=answer,
        route=route,  # type: ignore[arg-type]
        confidence=score,
        decision_factors={"faq_match_reason": "unique_exact_match"},
        trace_documents=[
            RetrievalDocument(
                chunk_id=chunk_id,
                title="标准知识",
                content=answer,
                source="cs_qa",
                metadata={
                    "document_id": chunk_id,
                    "answer": answer,
                    "review_status": "usable",
                    "retrieval_enabled": True,
                    "active": True,
                    "auto_reply_eligible": True,
                    "risk_level": "low",
                },
                rerank_score=score,
            )
        ],
        retrieval_counts={"rerank": 1},
    )


async def run_parity(
    tmp_path: Path,
    query: str,
    *,
    qa: FakeQA | None = None,
    goods_id: str | None = "p1",
    goods_name: str | None = "Water Cup",
    product: ProductProfile | None = None,
    product_answer: ProductAnswer | None = None,
    product_fail: bool = False,
    greeting: AgentReply | None = None,
    social: SocialDecision | None = None,
    intent_route: str = "rag",
    allow_auto: bool = True,
) -> ParityResult:
    qa = qa or FakeQA()
    products = FakeProducts(product)
    answers = FakeProductAnswers(product_answer, fail=product_fail)
    greeting_agent = FakeGreetingAgent(greeting)
    social_router = FixedSocialRouter(social)
    pdd_router = FixedPDDRouter(intent_route)
    connector = FakeConnector()
    settings = Settings(
        _env_file=None,
        default_shop_name=f"parity-{uuid4()}",
        auto_reply_rag_mode="immediate",
        rag_score_threshold=0.35,
        auto_reply_rag_min_margin=0.10,
        message_asset_dir=tmp_path / "assets",
        auto_reply_calibration_path=tmp_path / "calibration.json",
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'{uuid4()}.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))

    async def qa_provider():
        return qa

    legacy_runtime = ConsoleRuntime(
        repository,
        connector,
        qa_provider,
        settings,
        agent=greeting_agent,
        router=pdd_router,
        product_provider=products,
        product_answer_service=answers,
        social_router=social_router,
    )
    await legacy_runtime.ensure_initialized()
    assert legacy_runtime._shop is not None
    shop = legacy_runtime._shop
    await repository.set_global_automation(str(shop["id"]), allow_auto)
    async with repository._session_factory() as session:
        stored = Conversation(
            shop_id=str(shop["id"]),
            platform_conversation_id="buyer-1",
            display_name="顾客",
            goods_id=goods_id,
            goods_name=goods_name,
        )
        session.add(stored)
        await session.commit()
        await session.refresh(stored)
        conversation_id = stored.id
    conversation = await repository.get_conversation(conversation_id)
    assert conversation is not None
    batch = [message(query, goods_id=goods_id, goods_name=goods_name)]

    async def greeting_loader(shop_id):
        return await repository.get_greeting_config(str(shop_id))

    capabilities = AgentCapabilities(
        rules=default_rule_registry(),
        social_router=social_router,
        product_resolver=ProductResolver(),
        semantic_products=SemanticProductResolver(),
        product_answers=answers,
        fallbacks=ContextualFallbackService(llm=object()),
        qa_provider=qa_provider,
        conversations=ForbiddenUnifiedConversationStore(),
        products=products,
        greeting_agent=greeting_agent,
        greeting_config_loader=greeting_loader,
        pdd_router=pdd_router,
    )
    checkpoint = FileCheckpointStore(tmp_path / "checkpoints" / uuid4().hex)
    coordinator = StateCoordinator(checkpoint)
    agent_runtime = AgentRuntime(
        build_unified_chat_graph(capabilities, coordinator=coordinator),
        coordinator=coordinator,
    )
    state = await agent_runtime.invoke(
        pdd_context_to_agent_state(
            batch=batch,
            conversation=conversation,
            shop=shop,
        )
    )
    policy = AutoReplyPolicy(
        Calibration(),
        rag_mode=settings.auto_reply_rag_mode,
        rag_score_threshold=settings.rag_score_threshold,
        rag_min_margin=settings.auto_reply_rag_min_margin,
    )
    handoff = await repository.get_handoff_config(str(shop["id"]))
    blockers = [] if allow_auto else ["全局自动接待未开启"]
    graph_decision = evaluate_pdd_channel_policy(
        state,
        conversation,
        allow_auto=allow_auto,
        auto_reply_policy=policy,
        product_answer_service=answers,
        settings=settings,
        auto_reply_blockers=blockers,
        handoff_reply=str(handoff["reply_template"]),
    )
    graph_sent_count = len(connector.sent)

    await legacy_runtime._evaluate_batch(conversation_id, batch)
    await legacy_runtime._send_queue.join()
    legacy_decision = await repository.latest_decision(conversation_id)
    assert legacy_decision is not None
    await legacy_runtime.close()
    await engine.dispose()
    return ParityResult(
        legacy_decision,
        graph_decision,
        state,
        connector,
        graph_sent_count,
    )


def assert_decision_parity(result: ParityResult) -> None:
    legacy = result.legacy
    graph = result.graph
    assert graph.route == legacy["route"]
    assert graph.action == legacy["action"]
    assert graph.answer == legacy["suggested_answer"]
    assert graph.product_id == legacy.get("product_id")
    assert graph.product_name == legacy.get("product_name")
    assert graph.qa_code == legacy.get("qa_code")
    assert graph.top_score == legacy.get("top_score")
    assert graph.score_margin == legacy.get("score_margin")
    assert graph.reason == legacy.get("risk_reason")
    assert graph.confidence == legacy.get("top_score")
    assert (graph.action == "handoff") == graph_state_to_pdd_decision(
        result.state
    ).requires_human


def test_pdd_adapter_maps_batch_conversation_shop_and_product():
    batch = [
        message("hello", goods_id="p1", goods_name="Water Cup"),
        message("how to use", goods_id="p1", goods_name="Water Cup"),
    ]
    state = pdd_messages_to_agent_state(
        conversation_id="conv-1",
        shop_id="shop-1",
        customer_id="customer-1",
        batch=batch,
        goods_id="p1",
        goods_name="Water Cup",
        platform_conversation_id="buyer-1",
        display_name="顾客",
        shop_name="测试店铺",
    )
    assert state.session.channel == "pdd"
    assert state.session.shop_id == "shop-1"
    assert state.session.customer_id == "customer-1"
    assert state.session.current_product.product_id == "p1"
    assert state.turn.original_query == "hello\nhow to use"
    assert state.turn.product.recent_products[0].product_name == "Water Cup"
    assert state.context["pdd_platform_conversation_id"] == "buyer-1"
    assert len(state.messages) == 2


def test_pdd_adapter_keeps_product_id_when_name_is_missing():
    state = pdd_messages_to_agent_state(
        conversation_id="conv-1",
        shop_id="shop-1",
        customer_id=None,
        batch=[message("怎么使用", goods_id="p1", goods_name=None)],
        goods_id="p1",
        goods_name=None,
    )
    assert state.session.current_product.product_id == "p1"
    assert state.turn.product.reference.product_id == "p1"
    assert state.turn.product.recent_products == []


@pytest.mark.asyncio
async def test_greeting_legacy_graph_and_channel_policy_parity(tmp_path):
    result = await run_parity(
        tmp_path,
        "你好",
        greeting=AgentReply(
            intent="daily_greeting",
            greeting_type="salutation",
            confidence=1.0,
            recognition_source="rule",
            answer="您好，亲。",
        ),
    )
    assert_decision_parity(result)
    assert result.graph.route == "greeting"
    assert result.graph.action == "auto_send"


@pytest.mark.asyncio
async def test_social_legacy_graph_and_channel_policy_parity(tmp_path):
    result = await run_parity(
        tmp_path,
        "谢谢",
        social=SocialDecision(
            intent="small_talk",
            response="感谢您的认可。",
            reason_code="social_affection",
            source="rule",
            confidence=1.0,
        ),
    )
    assert_decision_parity(result)
    assert result.graph.route == "small_talk"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["我要投诉", "我要退款"])
async def test_complaint_and_refund_handoff_parity(tmp_path, query):
    result = await run_parity(tmp_path, query)
    assert_decision_parity(result)
    assert result.graph.action == "handoff"
    assert graph_state_to_pdd_decision(result.state).requires_human is True


@pytest.mark.asyncio
async def test_explicit_human_preserves_current_legacy_behavior(tmp_path):
    result = await run_parity(
        tmp_path,
        "我要人工",
        qa=FakeQA(rag=fallback_result()),
    )
    assert_decision_parity(result)
    assert result.graph.route == "fallback"
    assert result.graph.action == "suggest"
    assert graph_state_to_pdd_decision(result.state).requires_human is False


@pytest.mark.asyncio
async def test_faq_legacy_graph_and_auto_reply_policy_parity(tmp_path):
    result = await run_parity(tmp_path, "标准问法", qa=FakeQA(exact=faq_result()))
    assert_decision_parity(result)
    assert result.graph.route == "faq"
    assert result.graph.action == "auto_send"
    assert result.graph.qa_code == "faq-1"


@pytest.mark.asyncio
async def test_product_legacy_graph_and_channel_policy_parity(tmp_path):
    result = await run_parity(
        tmp_path,
        "怎么使用",
        intent_route="product",
        product=ProductProfile(id="p1", name="Water Cup", summary="cup"),
        product_answer=ProductAnswer(
            answer="water cup answer",
            facts_supported=True,
            contains_sensitive_or_after_sales=False,
            needs_clarification=False,
            confidence=0.98,
        ),
    )
    assert_decision_parity(result)
    assert result.graph.route == "product"
    assert result.graph.action == "auto_send"


@pytest.mark.asyncio
async def test_missing_product_context_handoff_parity(tmp_path):
    result = await run_parity(
        tmp_path,
        "怎么使用",
        intent_route="product",
        goods_id=None,
        goods_name=None,
    )
    assert_decision_parity(result)
    assert result.graph.action == "handoff"
    assert result.graph.answer is None


@pytest.mark.asyncio
async def test_product_lookup_failure_handoff_parity(tmp_path):
    result = await run_parity(
        tmp_path,
        "怎么使用",
        intent_route="product",
        product=None,
    )
    assert_decision_parity(result)
    assert result.graph.action == "handoff"
    assert result.graph.reason_code == "PRODUCT_DATA_MISSING"


@pytest.mark.asyncio
async def test_product_answer_failure_handoff_parity(tmp_path):
    result = await run_parity(
        tmp_path,
        "怎么使用",
        intent_route="product",
        product=ProductProfile(id="p1", name="Water Cup", summary="cup"),
        product_fail=True,
    )
    assert_decision_parity(result)
    assert result.graph.action == "handoff"


@pytest.mark.asyncio
async def test_rag_legacy_graph_and_auto_reply_policy_parity(tmp_path):
    result = await run_parity(
        tmp_path,
        "通用问题",
        qa=FakeQA(rag=rag_result()),
    )
    assert_decision_parity(result)
    assert result.graph.route == "rag"
    assert result.graph.action == "auto_send"


@pytest.mark.asyncio
async def test_knowledge_insufficient_fallback_policy_parity(tmp_path):
    result = await run_parity(
        tmp_path,
        "通用问题",
        qa=FakeQA(rag=fallback_result()),
    )
    assert_decision_parity(result)
    assert result.graph.route == "fallback"
    assert result.graph.action == "suggest"


@pytest.mark.asyncio
async def test_knowledge_failure_error_policy_parity(tmp_path):
    result = await run_parity(tmp_path, "通用问题", qa=FakeQA(fail=True))
    assert_decision_parity(result)
    assert result.graph.route == "error"
    assert result.graph.action == "suggest"


@pytest.mark.asyncio
async def test_channel_policy_stays_outside_graph_and_graph_has_no_sender(tmp_path):
    result = await run_parity(
        tmp_path,
        "标准问法",
        qa=FakeQA(exact=faq_result()),
    )
    assert_decision_parity(result)
    assert result.graph.action == "auto_send"
    assert result.graph_sent_count == 0
    assert result.connector.sent == ["FAQ 标准答案"]
    assert not hasattr(result.state, "outbound_job")
