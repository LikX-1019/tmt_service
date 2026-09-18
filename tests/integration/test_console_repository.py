from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.greeting import default_reply_templates, default_trigger_groups
from app.models import Base
from app.repositories.console_repository import ConsoleRepository, _iso


@pytest_asyncio.fixture
async def repository() -> ConsoleRepository:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield ConsoleRepository(factory)
    await engine.dispose()


def message_payload() -> dict[str, object]:
    return {
        "platform_conversation_id": "buyer-1",
        "display_name": "顾客 A",
        "avatar_url": "https://img.example/avatar-a.jpg",
        "direction": "inbound",
        "kind": "text",
        "content": "请问怎么使用？",
        "occurred_at": datetime(2026, 9, 15, tzinfo=timezone.utc),
        "platform_message_id": "message-1",
        "goods_id": "972793561880",
        "goods_name": "助眠眼罩",
        "is_backfill": False,
        "fingerprint": "f" * 64,
    }


def test_iso_marks_naive_database_datetimes_as_utc() -> None:
    assert _iso(datetime(2026, 9, 15, 7, 3, 45)) == "2026-09-15T07:03:45Z"
    assert _iso(datetime(2026, 9, 15, 7, 3, 45, tzinfo=timezone.utc)) == (
        "2026-09-15T07:03:45Z"
    )


@pytest.mark.asyncio
async def test_greeting_config_is_lazily_initialized_and_updates_immediately(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    initial = await repository.get_greeting_config(shop["id"])
    assert initial["enabled"] is True
    assert initial["trigger_groups"] == default_trigger_groups()
    assert initial["reply_templates"] == default_reply_templates()

    groups = default_trigger_groups()
    groups["salutation"] = ["你好呀"]
    templates = default_reply_templates()
    templates["salutation"] = ["新问候话术"]
    updated = await repository.update_greeting_config(
        shop["id"],
        enabled=False,
        trigger_groups=groups,
        reply_templates=templates,
    )
    assert updated["enabled"] is False
    assert updated["trigger_groups"]["salutation"] == ["你好呀"]
    assert updated["reply_templates"]["salutation"] == ["新问候话术"]
    assert await repository.get_greeting_config(shop["id"]) == updated


@pytest.mark.asyncio
async def test_missing_shop_has_no_greeting_config(
    repository: ConsoleRepository,
) -> None:
    with pytest.raises(LookupError):
        await repository.get_greeting_config("missing-shop")


@pytest.mark.asyncio
async def test_ingest_is_idempotent(repository: ConsoleRepository) -> None:
    shop = await repository.ensure_shop("测试店铺")
    conversation, _, created = await repository.ingest_message(shop["id"], message_payload())
    duplicate_conversation, _, duplicate_created = await repository.ingest_message(
        shop["id"], message_payload()
    )
    assert created is True
    assert duplicate_created is False
    assert conversation["id"] == duplicate_conversation["id"]
    assert conversation["response_deadline_at"] == duplicate_conversation["response_deadline_at"]
    assert len(await repository.list_messages(conversation["id"], limit=20)) == 1


@pytest.mark.asyncio
async def test_live_inbound_uses_server_time_and_configured_timeout(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    before = datetime.now(timezone.utc)
    conversation, _, _ = await repository.ingest_message(
        shop["id"], message_payload(), response_timeout_seconds=73
    )
    after = datetime.now(timezone.utc)

    started = datetime.fromisoformat(conversation["response_started_at"].replace("Z", "+00:00"))
    deadline = datetime.fromisoformat(conversation["response_deadline_at"].replace("Z", "+00:00"))
    assert before <= started <= after
    assert deadline - started == timedelta(seconds=73)
    assert started != message_payload()["occurred_at"]


@pytest.mark.asyncio
async def test_new_live_inbound_resets_timer_but_duplicate_does_not(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    first, _, _ = await repository.ingest_message(
        shop["id"], message_payload(), response_timeout_seconds=80
    )
    duplicate, _, created = await repository.ingest_message(
        shop["id"], message_payload(), response_timeout_seconds=20
    )
    second_payload = message_payload()
    second_payload.update({"platform_message_id": "message-2", "fingerprint": "2" * 64})
    second, _, _ = await repository.ingest_message(
        shop["id"], second_payload, response_timeout_seconds=120
    )

    assert created is False
    assert duplicate["response_deadline_at"] == first["response_deadline_at"]
    first_deadline = datetime.fromisoformat(first["response_deadline_at"].replace("Z", "+00:00"))
    second_deadline = datetime.fromisoformat(second["response_deadline_at"].replace("Z", "+00:00"))
    assert second_deadline > first_deadline


@pytest.mark.asyncio
async def test_conversation_state_suffix_does_not_create_duplicate_customer(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    payload = message_payload()
    payload.update(
        {
            "platform_customer_id": "1855811761-0-unTimeout",
            "platform_conversation_id": "1855811761-0-unTimeout",
        }
    )
    first, _, _ = await repository.ingest_message(shop["id"], payload)
    second, _ = await repository.upsert_customer_profile(
        shop["id"],
        {
            "platform_customer_id": "1855811761-0-all",
            "display_name": "顾客 A",
            "avatar_url": "https://img.example/avatar-a.jpg",
        },
    )
    assert first["id"] == second["id"]
    assert second["platform_conversation_id"] == "1855811761"
    assert len(await repository.list_customers(shop["id"])) == 1


@pytest.mark.asyncio
async def test_ingest_updates_customer_profile_without_overwriting_with_agent_name(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    conversation, _, _ = await repository.ingest_message(shop["id"], message_payload())
    update = message_payload()
    update.update(
        {
            "platform_message_id": "message-2",
            "fingerprint": "c" * 64,
            "display_name": "客服",
            "avatar_url": None,
        }
    )
    current, _, _ = await repository.ingest_message(shop["id"], update)
    assert current["id"] == conversation["id"]
    assert current["display_name"] == "顾客 A"
    assert current["avatar_url"] == "https://img.example/avatar-a.jpg"


@pytest.mark.asyncio
async def test_backfill_does_not_increment_unread_and_opening_clears_live_unread(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    backfill = message_payload()
    backfill["is_backfill"] = True
    conversation, _, _ = await repository.ingest_message(shop["id"], backfill)
    assert conversation["unread_count"] == 0
    assert conversation["response_deadline_at"] is None

    live = message_payload()
    live.update({"platform_message_id": "message-2", "fingerprint": "e" * 64})
    conversation, _, _ = await repository.ingest_message(shop["id"], live)
    assert conversation["unread_count"] == 1
    assert conversation["response_deadline_at"] is not None
    read = await repository.mark_conversation_read(conversation["id"])
    assert read["unread_count"] == 0
    assert read["response_deadline_at"] == conversation["response_deadline_at"]


@pytest.mark.asyncio
async def test_operator_can_clear_response_timer_without_changing_reception_state(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    conversation, _, _ = await repository.ingest_message(shop["id"], message_payload())

    cleared = await repository.clear_conversation_response_timer(conversation["id"])

    assert cleared["response_started_at"] is None
    assert cleared["response_deadline_at"] is None
    assert cleared["state"] == conversation["state"]
    assert cleared["auto_reply_enabled"] == conversation["auto_reply_enabled"]
    assert cleared["unread_count"] == conversation["unread_count"]


@pytest.mark.asyncio
async def test_live_outbound_clears_timer_but_backfill_outbound_does_not(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    inbound, _, _ = await repository.ingest_message(shop["id"], message_payload())
    assert inbound["response_deadline_at"] is not None

    backfill = message_payload()
    backfill.update(
        {
            "direction": "outbound",
            "is_backfill": True,
            "platform_message_id": "outbound-history",
            "fingerprint": "h" * 64,
        }
    )
    still_waiting, _, _ = await repository.ingest_message(shop["id"], backfill)
    assert still_waiting["response_deadline_at"] == inbound["response_deadline_at"]

    live = dict(backfill)
    live.update(
        {
            "is_backfill": False,
            "platform_message_id": "outbound-live",
            "fingerprint": "o" * 64,
        }
    )
    replied, _, _ = await repository.ingest_message(shop["id"], live)
    assert replied["response_started_at"] is None
    assert replied["response_deadline_at"] is None


@pytest.mark.asyncio
async def test_manual_reply_is_idempotent_and_takes_over(repository: ConsoleRepository) -> None:
    shop = await repository.ensure_shop("测试店铺")
    conversation, _, _ = await repository.ingest_message(shop["id"], message_payload())
    first, created = await repository.create_outbound_job(
        conversation["id"], client_request_id="manual:fixed", source="manual", content="您好"
    )
    second, created_again = await repository.create_outbound_job(
        conversation["id"], client_request_id="manual:fixed", source="manual", content="您好"
    )
    current = await repository.get_conversation(conversation["id"])
    assert created is True and created_again is False
    assert first["id"] == second["id"]
    assert second["content"] == "您好"
    assert current and current["state"] == "manual"
    assert current["auto_reply_enabled"] is False
    assert current["response_deadline_at"] is not None
    await repository.update_job(first["id"], status="sending")
    sending_conversation = await repository.get_conversation(conversation["id"])
    assert sending_conversation and sending_conversation["response_deadline_at"] is not None
    await repository.update_job(first["id"], status="sent")
    replied = await repository.get_conversation(conversation["id"])
    assert replied and replied["response_deadline_at"] is None
    with pytest.raises(ValueError, match="client_request_id"):
        await repository.create_outbound_job(
            conversation["id"],
            client_request_id="manual:fixed",
            source="manual",
            content="不同内容",
        )


@pytest.mark.asyncio
async def test_recovery_only_returns_never_clicked_jobs(repository: ConsoleRepository) -> None:
    shop = await repository.ensure_shop("测试店铺")
    conversation, _, _ = await repository.ingest_message(shop["id"], message_payload())
    queued, _ = await repository.create_outbound_job(
        conversation["id"], client_request_id="manual:queued", source="manual", content="A"
    )
    uncertain, _ = await repository.create_outbound_job(
        conversation["id"], client_request_id="manual:uncertain", source="manual", content="B"
    )
    await repository.update_job(uncertain["id"], status="sending")
    await repository.update_job(uncertain["id"], status="uncertain")
    sending, _ = await repository.create_outbound_job(
        conversation["id"], client_request_id="manual:sending", source="manual", content="C"
    )
    await repository.update_job(sending["id"], status="sending")
    recovered = await repository.recover_queued_jobs()
    assert [item["id"] for item in recovered] == [queued["id"]]
    recovered_sending = await repository.get_job(sending["id"])
    assert recovered_sending and recovered_sending["status"] == "uncertain"
    current = await repository.get_conversation(conversation["id"])
    assert current and current["response_deadline_at"] is not None


@pytest.mark.asyncio
async def test_conversations_with_nearest_deadline_are_listed_first(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    later_payload = message_payload()
    later_payload.update(
        {
            "platform_conversation_id": "buyer-later",
            "platform_message_id": "later-message",
            "fingerprint": "l" * 64,
        }
    )
    later, _, _ = await repository.ingest_message(
        shop["id"], later_payload, response_timeout_seconds=300
    )
    urgent_payload = message_payload()
    urgent_payload.update(
        {
            "platform_conversation_id": "buyer-urgent",
            "platform_message_id": "urgent-message",
            "fingerprint": "u" * 64,
        }
    )
    urgent, _, _ = await repository.ingest_message(
        shop["id"], urgent_payload, response_timeout_seconds=30
    )
    history_payload = message_payload()
    history_payload.update(
        {
            "platform_conversation_id": "buyer-history",
            "platform_message_id": "history-message",
            "fingerprint": "z" * 64,
            "is_backfill": True,
        }
    )
    history, _, _ = await repository.ingest_message(shop["id"], history_payload)

    items = await repository.list_conversations(shop["id"], limit=20)
    assert [item["id"] for item in items] == [urgent["id"], later["id"], history["id"]]


@pytest.mark.asyncio
async def test_messages_are_queryable_by_customer_day_and_responder(
    repository: ConsoleRepository,
) -> None:
    shop = await repository.ensure_shop("测试店铺")
    payload = message_payload()
    payload.update(
        {
            "platform_customer_id": "stable-buyer-1",
            "platform_conversation_id": "stable-buyer-1",
            "direction": "outbound",
            "sender_type": "agent",
            "sender_name": "客服甲",
            "occurred_at": datetime(2026, 9, 15, 17, 30, tzinfo=timezone.utc),
        }
    )
    conversation, stored, created = await repository.ingest_message(shop["id"], payload)
    customers = await repository.list_customers(shop["id"])
    messages = await repository.list_customer_messages(
        conversation["customer_id"],
        message_date=datetime(2026, 9, 16).date(),
    )
    assert created is True
    assert customers[0]["platform_customer_id"] == "stable-buyer-1"
    assert stored["message_date"] == "2026-09-16"
    assert stored["sender_display_name"] == "客服甲"
    assert stored["sender_account_id"]
    assert [item["id"] for item in messages] == [stored["id"]]
