import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.exceptions import ConsoleUnavailableError, InvalidRequestError
from app.integrations.pdd.base import (
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerServiceConnector,
    SendUncertainError,
)
from app.models import Base
from app.repositories.console_repository import ConsoleRepository
from app.services.console_runtime import ConsoleRuntime


class UncertainConnector(CustomerServiceConnector):
    def __init__(self) -> None:
        self.calls = 0
        self.current_status = ConnectorStatus.READY

    async def start(self, on_message, on_status, on_profile=None):
        return await self.status()

    async def stop(self):
        return ConnectorStatusSnapshot(ConnectorStatus.STOPPED)

    async def status(self):
        return ConnectorStatusSnapshot(self.current_status)

    async def send_message(self, platform_conversation_id, content):
        self.calls += 1
        raise SendUncertainError(
            "发送结果未知", clicked_at=datetime.now(timezone.utc)
        )


@pytest_asyncio.fixture
async def runtime_parts():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))
    connector = UncertainConnector()

    async def unused_qa_provider():
        raise AssertionError("人工发送不应调用 QA")

    runtime = ConsoleRuntime(repository, connector, unused_qa_provider, Settings())
    yield runtime, repository, connector
    await runtime.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_uncertain_send_is_not_retried_and_pauses_global_automation(runtime_parts) -> None:
    runtime, repository, connector = runtime_parts
    await runtime.ensure_initialized()
    shop = await repository.ensure_shop("测试店铺")
    await repository.set_global_automation(shop["id"], True)
    conversation, _, _ = await repository.ingest_message(
        shop["id"],
        {
            "platform_conversation_id": "buyer-1",
            "display_name": "顾客",
            "direction": "inbound",
            "kind": "text",
            "content": "您好",
            "occurred_at": datetime.now(timezone.utc),
            "platform_message_id": "m1",
            "fingerprint": "a" * 64,
            "is_backfill": False,
        },
    )
    other_conversation, _, _ = await repository.ingest_message(
        shop["id"],
        {
            "platform_conversation_id": "buyer-2",
            "display_name": "另一位顾客",
            "direction": "inbound",
            "kind": "text",
            "content": "在吗",
            "occurred_at": datetime.now(timezone.utc),
            "platform_message_id": "m2",
            "fingerprint": "c" * 64,
            "is_backfill": False,
        },
    )
    job, created = await repository.create_outbound_job(
        conversation["id"],
        content="您好，请问有什么可以帮您？",
        client_request_id="auto:test-1",
        source="auto",
    )
    assert created is True
    runtime._send_queue.put_nowait(str(job["id"]))
    await runtime._send_queue.join()
    stored = await repository.get_job(job["id"])
    current_shop = await repository.get_shop(shop["id"])
    failed_conversation = await repository.get_conversation(conversation["id"])
    unaffected_conversation = await repository.get_conversation(other_conversation["id"])
    assert connector.calls == 1
    assert stored and stored["status"] == "uncertain"
    assert current_shop and current_shop["global_auto_reply_enabled"] is True
    assert failed_conversation and failed_conversation["auto_reply_enabled"] is False
    assert unaffected_conversation and unaffected_conversation["auto_reply_enabled"] is True


@pytest.mark.asyncio
async def test_automatic_reception_requires_ready_connector(runtime_parts) -> None:
    runtime, _, connector = runtime_parts
    connector.current_status = ConnectorStatus.STOPPED
    with pytest.raises(ConsoleUnavailableError, match="连接器就绪"):
        await runtime.set_automation(True)
    assert (await runtime.automation())["enabled"] is False


@pytest.mark.asyncio
async def test_shop_summary_exposes_current_shop(runtime_parts) -> None:
    runtime, _, _ = runtime_parts
    summary = await runtime.shop_summary()

    assert summary["id"]
    assert summary["platform"] == "pdd"
    assert summary["name"]


@pytest.mark.asyncio
async def test_conversation_automatic_reception_requires_global_permission(
    runtime_parts,
) -> None:
    runtime, repository, _ = runtime_parts
    await runtime.ensure_initialized()
    shop = await repository.ensure_shop("测试店铺")
    conversation, _, _ = await repository.ingest_message(
        shop["id"],
        {
            "platform_conversation_id": "buyer-permission",
            "display_name": "权限测试顾客",
            "direction": "inbound",
            "kind": "text",
            "content": "您好",
            "occurred_at": datetime.now(timezone.utc),
            "platform_message_id": "permission-m1",
            "fingerprint": "b" * 64,
            "is_backfill": False,
        },
    )
    await repository.set_conversation_automation(conversation["id"], False)
    with pytest.raises(InvalidRequestError, match="全局自动接待"):
        await runtime.set_conversation_automation(conversation["id"], True)
    manual = await runtime.set_conversation_automation(conversation["id"], False)
    assert manual["state"] == "manual"
    assert manual["auto_reply_enabled"] is False


def test_event_audit_removes_message_and_reply_body() -> None:
    payload = {"id": "m1", "conversation_id": "c1", "content": "顾客隐私正文", "kind": "text"}
    sanitized = ConsoleRuntime._sanitize_event_payload("message.created", payload)
    assert sanitized == {"id": "m1", "conversation_id": "c1", "kind": "text"}


def test_conversation_event_includes_response_timer_fields() -> None:
    payload = {
        "id": "c1",
        "state": "pending",
        "unread_count": 1,
        "response_started_at": "2026-09-17T00:00:00Z",
        "response_deadline_at": "2026-09-17T00:02:40Z",
        "display_name": "不应进入事件审计",
    }
    assert ConsoleRuntime._sanitize_event_payload("conversation.upserted", payload) == {
        "id": "c1",
        "state": "pending",
        "unread_count": 1,
        "response_started_at": "2026-09-17T00:00:00Z",
        "response_deadline_at": "2026-09-17T00:02:40Z",
    }


@pytest.mark.asyncio
async def test_cancelled_debounce_does_not_remove_replacement(runtime_parts) -> None:
    runtime, _, _ = runtime_parts
    first = asyncio.create_task(runtime._evaluate_after_delay("c1"))
    runtime._debounces["c1"] = first
    first.cancel()
    second = asyncio.create_task(runtime._evaluate_after_delay("c1"))
    runtime._debounces["c1"] = second
    await asyncio.gather(first, return_exceptions=True)
    assert runtime._debounces["c1"] is second
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
