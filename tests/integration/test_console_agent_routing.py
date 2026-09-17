from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.service import AgentReply, CustomerContext
from app.core.config import Settings
from app.integrations.pdd.base import (
    BrowserMessage,
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerServiceConnector,
)
from app.models import Base, Conversation
from app.repositories.console_repository import ConsoleRepository
from app.services.console_runtime import ConsoleRuntime


class ReadyConnector(CustomerServiceConnector):
    async def start(self, on_message, on_status, on_profile=None):
        return await self.status()

    async def stop(self):
        return ConnectorStatusSnapshot(ConnectorStatus.STOPPED)

    async def status(self):
        return ConnectorStatusSnapshot(ConnectorStatus.READY)

    async def send_message(self, platform_conversation_id, content):
        raise AssertionError("自动接待未开启时不应发送")


class GreetingAgent:
    def __init__(self) -> None:
        self.customer: CustomerContext | None = None
        self.greeting_config: dict | None = None

    async def run(
        self,
        message: str,
        customer: CustomerContext,
        *,
        greeting_config: dict | None = None,
    ) -> AgentReply:
        self.customer = customer
        self.greeting_config = greeting_config
        return AgentReply(
            intent="daily_greeting",
            greeting_type="salutation",
            confidence=0.98,
            recognition_source="rule",
            answer="林女士您好，我是小满，请问有什么可以帮您？",
        )


@pytest.mark.asyncio
async def test_greeting_routes_to_agent_before_qa() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    repository = ConsoleRepository(session_factory)
    agent = GreetingAgent()

    async def qa_provider():
        raise AssertionError("日常打招呼不应进入 QA/RAG")

    runtime = ConsoleRuntime(
        repository,
        ReadyConnector(),
        qa_provider,
        Settings(_env_file=None, default_shop_name="JAFFICK旗舰店"),
        agent=agent,
    )
    try:
        await runtime.ensure_initialized()
        shop = await repository.ensure_shop("JAFFICK旗舰店")
        async with session_factory() as session:
            stored_conversation = Conversation(
                shop_id=shop["id"],
                platform_conversation_id="buyer-greeting",
                display_name="林女士",
                goods_id="sku-1",
                goods_name="轻薄羽绒服",
            )
            session.add(stored_conversation)
            await session.commit()
            conversation_id = stored_conversation.id
        message = BrowserMessage(
            platform_conversation_id="buyer-greeting",
            platform_customer_id="buyer-greeting",
            display_name="林女士",
            direction="inbound",
            kind="text",
            content="你好",
            occurred_at=datetime.now(timezone.utc),
            platform_message_id="greeting-m1",
            fingerprint="c" * 64,
        )

        await runtime._evaluate_batch(conversation_id, [message])

        decision = await repository.latest_decision(conversation_id)
        assert decision is not None
        assert decision["route"] == "greeting"
        assert decision["action"] == "suggest"
        assert decision["greeting_type"] == "salutation"
        assert decision["recognition_source"] == "rule"
        assert decision["suggested_answer"] == "林女士您好，我是小满，请问有什么可以帮您？"
        assert agent.customer is not None
        assert agent.customer.display_name == "林女士"
        assert agent.customer.shop_name == "JAFFICK旗舰店"
        assert agent.customer.goods_name == "轻薄羽绒服"
        assert agent.greeting_config is not None
        assert agent.greeting_config["enabled"] is True
    finally:
        await runtime.close()
        await engine.dispose()
