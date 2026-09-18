from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.routing import CustomerServiceRouter
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
from app.services.console_runtime import ConsoleRuntime
from app.services.product_service import ProductAnswer, ProductProfile


class ReadyConnector(CustomerServiceConnector):
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


class OtherAgent:
    async def run(self, *_args, **_kwargs) -> AgentReply:
        return AgentReply(intent="other", confidence=1.0)


class StubQA:
    def __init__(self, faq: QAResult | None = None) -> None:
        self.faq = faq
        self.rag_called = False

    def match_exact(self, *_args, **_kwargs):
        return self.faq

    async def answer_rag(self, *_args, **_kwargs):
        self.rag_called = True
        return QAResult(answer="通用知识", route="fallback")


class StubProductProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_product(self, product_id: str) -> ProductProfile:
        self.calls += 1
        return ProductProfile(id=product_id, name="护腕", summary="日常佩戴支撑")


class StubProductAnswerService:
    async def answer(self, _query: str, _product: ProductProfile) -> ProductAnswer:
        return ProductAnswer(
            answer="这款适合日常佩戴。",
            facts_supported=True,
            contains_sensitive_or_after_sales=False,
            needs_clarification=False,
            confidence=0.99,
        )

    @staticmethod
    def can_auto_send(answer, *, allow_auto, settings):
        return answer.facts_supported and allow_auto and answer.confidence >= settings.product_auto_reply_min_confidence


def message(content: str, fingerprint: str) -> BrowserMessage:
    return BrowserMessage(
        platform_conversation_id="buyer-1",
        platform_customer_id="buyer-1",
        display_name="顾客",
        direction="inbound",
        kind="text",
        content=content,
        occurred_at=datetime.now(timezone.utc),
        fingerprint=fingerprint,
    )


@pytest.mark.asyncio
async def test_product_path_auto_sends_only_after_exact_qa_miss() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))
    connector = ReadyConnector()
    qa = StubQA()

    async def qa_provider():
        return qa

    runtime = ConsoleRuntime(
        repository,
        connector,
        qa_provider,
        Settings(_env_file=None),
        agent=OtherAgent(),
        router=CustomerServiceRouter(),
        product_provider=StubProductProvider(),
        product_answer_service=StubProductAnswerService(),
    )
    try:
        await runtime.ensure_initialized()
        shop = await repository.ensure_shop("测试店铺")
        await repository.set_global_automation(shop["id"], True)
        async with repository._session_factory() as session:
            conversation = Conversation(
                shop_id=shop["id"],
                platform_conversation_id="buyer-1",
                display_name="顾客",
                goods_id="sku-1",
                goods_name="护腕",
            )
            session.add(conversation)
            await session.commit()
            conversation_id = conversation.id

        await runtime._evaluate_batch(conversation_id, [message("材质是什么", "a" * 64)])
        await runtime._send_queue.join()
        decision = await repository.latest_decision(conversation_id)
        assert decision is not None
        assert decision["route"] == "product"
        assert decision["action"] == "auto_send"
        assert decision["product_id"] == "sku-1"
        assert decision["product_name"] == "护腕"
        assert connector.sent == ["这款适合日常佩戴。"]
        assert qa.rag_called is False
    finally:
        await runtime.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_handoff_precedes_faq_and_keeps_conversation_manual() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))
    connector = ReadyConnector()
    faq = QAResult(
        answer="不应使用",
        route="faq",
        trace_documents=[
            RetrievalDocument(
                chunk_id="FAQ-1",
                content="回答",
                metadata={"answer": "不应使用"},
                rerank_score=1.0,
            )
        ],
    )

    async def qa_provider():
        return StubQA(faq)

    runtime = ConsoleRuntime(repository, connector, qa_provider, Settings(_env_file=None), agent=OtherAgent())
    try:
        await runtime.ensure_initialized()
        shop = await repository.ensure_shop("测试店铺")
        conversation, _, _ = await repository.ingest_message(
            shop["id"],
            {
                "platform_conversation_id": "buyer-1",
                "display_name": "顾客",
                "direction": "inbound",
                "kind": "text",
                "content": "占位",
                "occurred_at": datetime.now(timezone.utc),
                "fingerprint": "b" * 64,
            },
        )

        await runtime._evaluate_batch(conversation["id"], [message("我要退货", "c" * 64)])
        await runtime._send_queue.join()
        decision = await repository.latest_decision(conversation["id"])
        current = await repository.get_conversation(conversation["id"])
        assert decision and decision["route"] == "handoff"
        assert decision["action"] == "handoff"
        assert current and current["state"] == "manual"
        assert current["auto_reply_enabled"] is False
        assert connector.sent == ["您好，您反馈的售后问题已为您转接人工客服处理，请您稍候。"]
    finally:
        await runtime.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_exact_faq_prevents_product_and_rag_paths() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))
    connector = ReadyConnector()
    faq = QAResult(
        answer="标准答案",
        route="faq",
        confidence=1.0,
        trace_documents=[
            RetrievalDocument(
                chunk_id="FAQ-1",
                content="标准答案",
                metadata={
                    "answer": "标准答案",
                    "review_status": "usable",
                    "retrieval_enabled": True,
                    "auto_reply_eligible": False,
                    "risk_level": "low",
                },
                rerank_score=1.0,
            )
        ],
    )
    qa = StubQA(faq)
    provider = StubProductProvider()

    async def qa_provider():
        return qa

    runtime = ConsoleRuntime(
        repository,
        connector,
        qa_provider,
        Settings(_env_file=None),
        agent=OtherAgent(),
        product_provider=provider,
    )
    try:
        await runtime.ensure_initialized()
        shop = await repository.ensure_shop("测试店铺")
        async with repository._session_factory() as session:
            conversation = Conversation(
                shop_id=shop["id"],
                platform_conversation_id="buyer-1",
                display_name="顾客",
                goods_id="sku-1",
                goods_name="护腕",
            )
            session.add(conversation)
            await session.commit()
            conversation_id = conversation.id

        await runtime._evaluate_batch(conversation_id, [message("普通精确问法", "f" * 64)])
        decision = await repository.latest_decision(conversation_id)
        gaps = await repository.list_knowledge_gaps(shop["id"])
        assert decision and decision["route"] == "faq"
        assert provider.calls == 0
        assert qa.rag_called is False
        assert gaps == []
    finally:
        await runtime.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_product_question_without_goods_card_transfers_without_message() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))
    connector = ReadyConnector()

    async def qa_provider():
        return StubQA()

    runtime = ConsoleRuntime(
        repository,
        connector,
        qa_provider,
        Settings(_env_file=None),
        agent=OtherAgent(),
        router=CustomerServiceRouter(),
    )
    try:
        await runtime.ensure_initialized()
        shop = await repository.ensure_shop("测试店铺")
        conversation, _, _ = await repository.ingest_message(
            shop["id"],
            {
                "platform_conversation_id": "buyer-1",
                "display_name": "顾客",
                "direction": "inbound",
                "kind": "text",
                "content": "占位",
                "occurred_at": datetime.now(timezone.utc),
                "fingerprint": "d" * 64,
            },
        )

        await runtime._evaluate_batch(conversation["id"], [message("材质是什么", "e" * 64)])
        decision = await repository.latest_decision(conversation["id"])
        current = await repository.get_conversation(conversation["id"])
        assert decision and decision["route"] == "handoff"
        assert decision["suggested_answer"] is None
        assert current and current["state"] == "manual"
        assert connector.sent == []
    finally:
        await runtime.close()
        await engine.dispose()
