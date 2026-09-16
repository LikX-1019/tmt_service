from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
async def test_ingest_is_idempotent(repository: ConsoleRepository) -> None:
    shop = await repository.ensure_shop("测试店铺")
    conversation, _, created = await repository.ingest_message(shop["id"], message_payload())
    duplicate_conversation, _, duplicate_created = await repository.ingest_message(
        shop["id"], message_payload()
    )
    assert created is True
    assert duplicate_created is False
    assert conversation["id"] == duplicate_conversation["id"]
    assert len(await repository.list_messages(conversation["id"], limit=20)) == 1


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

    live = message_payload()
    live.update({"platform_message_id": "message-2", "fingerprint": "e" * 64})
    conversation, _, _ = await repository.ingest_message(shop["id"], live)
    assert conversation["unread_count"] == 1
    read = await repository.mark_conversation_read(conversation["id"])
    assert read["unread_count"] == 0


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
