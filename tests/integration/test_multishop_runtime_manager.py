from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.integrations.pdd.base import (
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerServiceConnector,
    SendReceipt,
    ShopIdentity,
)
from app.models import Base, QAKnowledge
from app.repositories.console_repository import ConsoleRepository
from app.services.shop_runtime_manager import ShopRuntimeManager


class FakeConnector(CustomerServiceConnector):
    def __init__(self, identity: ShopIdentity | None = None) -> None:
        self.current = ConnectorStatus.STOPPED
        self.starts = 0
        self.focused = False
        self.shop_identity = identity

    async def start(self, on_message, on_status, on_profile=None):
        self.starts += 1
        self.current = ConnectorStatus.READY
        snapshot = await self.status()
        await on_status(snapshot)
        return snapshot

    async def stop(self):
        self.current = ConnectorStatus.STOPPED
        return await self.status()

    async def status(self):
        return ConnectorStatusSnapshot(self.current)

    async def send_message(self, platform_conversation_id, content):
        now = datetime.now(timezone.utc)
        return SendReceipt(clicked_at=now, confirmed_at=now)

    async def identity(self):
        return self.shop_identity

    async def focus(self):
        self.focused = True


@pytest_asyncio.fixture
async def repository():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repo = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))
    yield repo
    await engine.dispose()


def payload(conversation_id: str) -> dict[str, object]:
    return {
        "platform_conversation_id": conversation_id,
        "platform_customer_id": conversation_id,
        "display_name": "同名顾客",
        "direction": "inbound",
        "kind": "text",
        "content": "同一条平台消息",
        "occurred_at": datetime.now(timezone.utc),
        "platform_message_id": "same-message-id",
        "fingerprint": "a" * 64,
        "is_backfill": False,
    }


@pytest.mark.asyncio
async def test_message_and_request_idempotency_are_isolated_by_shop(repository):
    first = await repository.create_provisioning_shop(browser_profile_key="first")
    second = await repository.create_provisioning_shop(browser_profile_key="second")
    first_conversation, first_message, _ = await repository.ingest_message(
        first["id"], payload("same-customer")
    )
    second_conversation, second_message, _ = await repository.ingest_message(
        second["id"], payload("same-customer")
    )

    first_job, first_created = await repository.create_outbound_job(
        first_conversation["id"],
        client_request_id="manual:same-key",
        source="manual",
        content="第一家店回复",
    )
    second_job, second_created = await repository.create_outbound_job(
        second_conversation["id"],
        client_request_id="manual:same-key",
        source="manual",
        content="第二家店回复",
    )

    assert first_message["id"] != second_message["id"]
    assert first_conversation["shop_id"] == first["id"]
    assert second_conversation["shop_id"] == second["id"]
    assert first_created is True and second_created is True
    assert first_job["shop_id"] == first["id"]
    assert second_job["shop_id"] == second["id"]


@pytest.mark.asyncio
async def test_manager_restores_only_desired_online_shops(repository):
    online = await repository.create_provisioning_shop(browser_profile_key="online")
    offline = await repository.create_provisioning_shop(browser_profile_key="offline")
    await repository.update_shop_state(
        online["id"], lifecycle_status="active", desired_online=True
    )
    await repository.update_shop_state(
        offline["id"], lifecycle_status="active", desired_online=False
    )
    connectors: dict[str, FakeConnector] = {}

    def factory(shop, slot):
        connector = FakeConnector()
        connectors[shop["id"]] = connector
        return connector

    async def qa_provider():
        raise AssertionError("恢复连接器不应加载 QA")

    manager = ShopRuntimeManager(
        repository,
        qa_provider,
        Settings(_env_file=None),
        connector_factory=factory,
    )
    try:
        await manager.initialize()
        assert connectors[online["id"]].starts == 1
        assert connectors[offline["id"]].starts == 0
        assert manager._profile_path(online).name == "online"
        assert manager._profile_path(offline).name == "offline"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_duplicate_platform_account_marks_new_shop_failed(repository):
    existing = await repository.create_provisioning_shop(browser_profile_key="existing")
    current, duplicate = await repository.identify_shop(
        existing["id"], platform_shop_id="mall-100", name="已有店铺"
    )
    assert duplicate is None
    assert current["lifecycle_status"] == "active"

    candidate = await repository.create_provisioning_shop(browser_profile_key="candidate")
    manager = ShopRuntimeManager(
        repository,
        lambda: None,
        Settings(_env_file=None),
        connector_factory=lambda shop, slot: FakeConnector(),
    )
    async with manager.broker.subscribe() as queue:
        await manager._identify(
            candidate["id"], ShopIdentity(platform_shop_id="mall-100", name="重复登录")
        )
        event = await asyncio.wait_for(queue.get(), timeout=1)
    rejected = await repository.get_shop(candidate["id"])

    assert event["event_type"] == "shop.duplicate"
    assert event["payload"]["existing_shop_id"] == existing["id"]
    assert event["payload"]["shop_id"] == candidate["id"]
    assert rejected is not None
    assert rejected["lifecycle_status"] == "failed"
    assert rejected["desired_online"] is False
    assert rejected["last_error_code"] == "DUPLICATE_SHOP_ACCOUNT"


@pytest.mark.asyncio
async def test_knowledge_gaps_aggregate_and_customer_notes_are_shop_scoped(repository):
    first = await repository.create_provisioning_shop(browser_profile_key="notes-first")
    second = await repository.create_provisioning_shop(browser_profile_key="notes-second")
    conversation, _, _ = await repository.ingest_message(
        first["id"], payload("note-customer")
    )

    initial = await repository.record_knowledge_gap(
        first["id"],
        product_id="sku-1",
        normalized_question="怎么使用",
        example_question="这个怎么使用？",
        reason_code="LOW_CONFIDENCE",
    )
    repeated = await repository.record_knowledge_gap(
        first["id"],
        product_id="sku-1",
        normalized_question="怎么使用",
        example_question="请问怎么使用",
        reason_code="LOW_CONFIDENCE",
    )
    note = await repository.update_customer_note(
        first["id"],
        conversation["customer_id"],
        content="需要人工跟进",
        tags=["重点", "重点", "售后"],
        pinned=True,
    )
    draft = await repository.create_knowledge_gap_qa_draft(
        first["id"],
        initial["id"],
        standard_answer="请按说明书中的步骤使用。",
    )
    repeated_draft = await repository.create_knowledge_gap_qa_draft(
        first["id"],
        initial["id"],
        standard_answer="重试请求不应覆盖已创建草稿。",
    )
    async with repository._session_factory() as session:
        qa = await session.scalar(
            select(QAKnowledge).where(QAKnowledge.qa_code == draft["linked_qa_code"])
        )

    assert repeated["id"] == initial["id"]
    assert repeated["occurrences"] == 2
    assert note["tags"] == ["重点", "售后"]
    assert draft["status"] == "draft"
    assert repeated_draft["candidate_answer"] == "请按说明书中的步骤使用。"
    assert qa is not None
    assert qa.status == "draft"
    assert qa.review_status == "pending_validation"
    assert qa.retrieval_enabled is False
    assert qa.auto_reply_eligible is False
    assert qa.human_required is False
    with pytest.raises(LookupError):
        await repository.get_customer_note(
            second["id"], conversation["customer_id"]
        )
    with pytest.raises(LookupError):
        await repository.create_knowledge_gap_qa_draft(
            second["id"],
            initial["id"],
            standard_answer="跨店铺答案",
        )


@pytest.mark.asyncio
async def test_legacy_runtime_requires_unambiguous_active_shop(repository):
    first = await repository.create_provisioning_shop(browser_profile_key="legacy-a")
    second = await repository.create_provisioning_shop(browser_profile_key="legacy-b")
    await repository.update_shop_state(first["id"], lifecycle_status="active")
    await repository.update_shop_state(second["id"], lifecycle_status="active")

    async def qa_provider():
        raise AssertionError("兼容路由判定不应加载 QA")

    manager = ShopRuntimeManager(
        repository,
        qa_provider,
        Settings(_env_file=None),
        connector_factory=lambda shop, slot: FakeConnector(),
    )
    try:
        await manager.initialize()
        from app.core.exceptions import ShopContextRequiredError

        with pytest.raises(ShopContextRequiredError):
            await manager.legacy_runtime()
    finally:
        await manager.close()
