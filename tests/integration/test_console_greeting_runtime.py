from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.greeting import default_reply_templates, default_trigger_groups
from app.agent.service import CustomerServiceAgent
from app.core.config import Settings
from app.integrations.pdd.base import (
    BrowserMessage,
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerServiceConnector,
    SendReceipt,
)
from app.models import Base, Conversation, OutboundJob
from app.repositories.console_repository import ConsoleRepository
from app.services.console_runtime import ConsoleRuntime
from tests.integration.console_graph import build_console_graph_runtime


class ReadyConnector(CustomerServiceConnector):
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def start(self, on_message, on_status, on_profile=None):
        return await self.status()

    async def stop(self):
        return ConnectorStatusSnapshot(ConnectorStatus.STOPPED)

    async def status(self):
        return ConnectorStatusSnapshot(ConnectorStatus.READY)

    async def send_message(self, platform_conversation_id, content):
        now = datetime.now(timezone.utc)
        self.sent.append((platform_conversation_id, content))
        return SendReceipt(clicked_at=now, confirmed_at=now)


class NeverCalledLLM:
    async def ainvoke(self, messages):
        raise AssertionError("规则命中的问候不应调用模型")


def message(content: str, sequence: int) -> BrowserMessage:
    return BrowserMessage(
        platform_conversation_id="buyer-greeting",
        platform_customer_id="buyer-greeting",
        display_name="林女士",
        direction="inbound",
        kind="text",
        content=content,
        occurred_at=datetime.now(timezone.utc),
        platform_message_id=f"greeting-m{sequence}",
        fingerprint=f"{sequence:x}" * 32,
    )


@pytest.mark.asyncio
async def test_rule_greeting_config_is_immediate_and_respects_send_gates() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    repository = ConsoleRepository(session_factory)
    connector = ReadyConnector()

    async def qa_provider():
        raise AssertionError("规则命中的问候不应进入 QA/RAG")

    settings = Settings(_env_file=None, default_shop_name="JAFFICK旗舰店")
    greeting_agent = CustomerServiceAgent(intent_llm=NeverCalledLLM())
    runtime = ConsoleRuntime(
        repository,
        connector,
        settings,
        agent_runtime=build_console_graph_runtime(
            repository,
            qa_provider,
            settings,
            agent=greeting_agent,
        ),
    )
    try:
        await runtime.ensure_initialized()
        shop = await repository.ensure_shop("JAFFICK旗舰店")
        async with session_factory() as session:
            stored = Conversation(
                shop_id=shop["id"],
                platform_conversation_id="buyer-greeting",
                display_name="林女士",
            )
            session.add(stored)
            await session.commit()
            conversation_id = stored.id

        await runtime._evaluate_batch(conversation_id, [message("你好", 1)])
        first = await repository.latest_decision(conversation_id)
        assert first["action"] == "suggest"
        assert first["recognition_source"] == "rule"
        assert first["risk_reason"] == "规则识别为一般问候；全局自动接待未开启，仅生成建议"
        assert first["suggested_answer"] in default_reply_templates()["salutation"]

        templates = default_reply_templates()
        templates["availability"] = ["中台刚保存的在线话术"]
        await repository.update_greeting_config(
            shop["id"],
            enabled=True,
            trigger_groups=default_trigger_groups(),
            reply_templates=templates,
        )
        await runtime._evaluate_batch(conversation_id, [message("在吗", 2)])
        second = await repository.latest_decision(conversation_id)
        assert second["greeting_type"] == "availability"
        assert second["suggested_answer"] == "中台刚保存的在线话术"

        await runtime.set_automation(True)
        await runtime._evaluate_batch(conversation_id, [message("拜拜", 3)])
        await runtime._send_queue.join()
        third = await repository.latest_decision(conversation_id)
        assert third["action"] == "auto_send"
        assert third["greeting_type"] == "goodbye"
        assert len(connector.sent) == 1
        assert connector.sent[0][0] == "buyer-greeting"
        assert connector.sent[0][1] in default_reply_templates()["goodbye"]
        async with session_factory() as session:
            job = await session.scalar(select(OutboundJob))
            assert job is not None
            assert job.client_request_id.startswith("auto:")
            assert len(job.client_request_id) <= 64
    finally:
        await runtime.close()
        await engine.dispose()
