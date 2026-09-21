"""G5B PDD production cutover and channel-side side-effect boundaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.console_runtime as console_runtime_module
from app.agent.channels.pdd_adapter import PDDChannelDecision
from app.agent.runtime import AgentRuntime
from app.core.config import Settings
from app.integrations.pdd.base import (
    BrowserMessage,
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerServiceConnector,
    SendReceipt,
)
from app.models import Base, Conversation, OutboundJob, ReplyDecision
from app.repositories.console_repository import ConsoleRepository
from app.services.console_runtime import ConsoleRuntime
from app.services.shop_runtime_manager import ShopRuntimeManager


class RecordingConnector(CustomerServiceConnector):
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


class StubAgentRuntime:
    async def invoke(self, state):
        return state


def _message(content: str = "你好") -> BrowserMessage:
    return BrowserMessage(
        platform_conversation_id="buyer-1",
        platform_customer_id="buyer-1",
        display_name="顾客",
        direction="inbound",
        kind="text",
        content=content,
        occurred_at=datetime.now(timezone.utc),
        platform_message_id="message-1",
        fingerprint=(content.encode().hex() + "0" * 64)[:64],
    )


@pytest_asyncio.fixture
async def console_runtime(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'console.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))
    connector = RecordingConnector()
    settings = Settings(
        _env_file=None,
        default_shop_name="G5B 测试店铺",
        message_asset_dir=tmp_path / "assets",
        auto_reply_calibration_path=tmp_path / "calibration.json",
    )
    runtime = ConsoleRuntime(
        repository,
        connector,
        lambda: None,
        settings,
        agent_runtime=StubAgentRuntime(),  # type: ignore[arg-type]
    )
    await runtime.ensure_initialized()
    assert runtime._shop is not None
    shop = runtime._shop
    async with repository._session_factory() as session:
        conversation = Conversation(
            shop_id=shop["id"],
            platform_conversation_id="buyer-1",
            display_name="顾客",
            goods_id="p1",
            goods_name="测试商品",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
    yield runtime, repository, connector, str(conversation.id), engine
    await runtime.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_shop_manager_injects_graph_runtime_by_default(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'manager.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ConsoleRepository(async_sessionmaker(engine, expire_on_commit=False))
    settings = Settings(_env_file=None, default_shop_name="manager-test")
    manager = ShopRuntimeManager(
        repository,
        lambda: None,
        settings,
        connector_factory=lambda shop, slot: RecordingConnector(),
    )
    shop = await repository.create_provisioning_shop(browser_profile_key="manager")
    runtime = manager._build_runtime(shop, 0)

    assert isinstance(manager._agent_runtime, AgentRuntime)
    assert runtime._agent_runtime is manager._agent_runtime
    assert not hasattr(runtime, "_pdd_runtime_mode")

    await manager.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_pdd_legacy_runtime_switch_and_method_do_not_exist(console_runtime):
    runtime, _, _, _, _ = console_runtime

    assert "pdd_agent_runtime" not in type(runtime._settings).model_fields
    assert not hasattr(runtime, "_pdd_runtime_mode")
    assert not hasattr(runtime, "_evaluate_batch_legacy")
    assert not hasattr(runtime, "_evaluate_batch_graph")


@pytest.mark.asyncio
async def test_graph_failure_does_not_call_legacy_or_create_outbound_job(
    console_runtime, monkeypatch
):
    runtime, repository, connector, conversation_id, _ = console_runtime
    async def fail_graph(*args: Any, **kwargs: Any):
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr(console_runtime_module, "invoke_pdd_agent", fail_graph)

    await runtime._evaluate_batch(conversation_id, [_message("知识问题")])
    await runtime._send_queue.join()

    decision = await repository.latest_decision(conversation_id)
    assert decision is not None
    assert decision["route"] == "error"
    assert decision["action"] == "suggest"
    assert decision["suggested_answer"] is None
    assert connector.sent == []
    assert not hasattr(runtime, "_evaluate_batch_legacy")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "route", "answer"),
    [
        ("auto_send", "greeting", "自动回复"),
        ("suggest", "faq", "建议回复"),
        ("handoff", "handoff", None),
    ],
)
async def test_graph_side_effects_are_channel_owned_and_idempotent(
    console_runtime,
    monkeypatch,
    action: str,
    route: str,
    answer: str | None,
):
    runtime, repository, connector, conversation_id, _ = console_runtime
    decision = PDDChannelDecision(
        route=route,
        action=action,
        answer=answer,
        reason="测试决策",
    )

    async def fake_invoke(*args: Any, **kwargs: Any):
        return object()

    def fake_policy(*args: Any, **kwargs: Any):
        return decision

    monkeypatch.setattr(console_runtime_module, "invoke_pdd_agent", fake_invoke)
    monkeypatch.setattr(
        console_runtime_module,
        "evaluate_pdd_channel_policy",
        fake_policy,
    )

    await runtime._evaluate_batch(conversation_id, [_message(route)])
    # Graph/policy evaluation never touches the connector; only the queued worker may.
    assert connector.sent == []
    await runtime._send_queue.join()

    async with repository._session_factory() as session:
        decisions = list(
            (
                await session.scalars(
                    select(ReplyDecision).where(
                        ReplyDecision.conversation_id == conversation_id
                    )
                )
            ).all()
        )
        jobs = list(
            (
                await session.scalars(
                    select(OutboundJob).where(
                        OutboundJob.conversation_id == conversation_id
                    )
                )
            ).all()
        )
        conversation = await session.get(Conversation, conversation_id)

    assert len(decisions) == 1
    if action == "auto_send":
        assert len(jobs) == 1
        assert jobs[0].status == "sent"
        assert connector.sent == [answer]
    elif action == "suggest":
        assert jobs == []
        assert connector.sent == []
    else:
        assert jobs == []
        assert connector.sent == []
        assert conversation is not None
        assert conversation.state == "manual"
        assert conversation.auto_reply_enabled is False
