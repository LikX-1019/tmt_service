from datetime import datetime, timezone

import httpx
import pytest

from app.api.dependencies import get_console_runtime, get_shop_runtime_manager
from app.api.v1.console import events
from app.services.event_broker import EventBroker
from main import app


NOW = datetime(2026, 9, 15, tzinfo=timezone.utc).isoformat()


class FakeRepository:
    async def events_after(self, event_id: int):
        assert event_id == 4
        return [{"id": 5, "event_type": "message.created", "payload": {"id": "m1"}}]

    async def latest_event_id(self):
        return 5


class FakeRuntime:
    def __init__(self) -> None:
        self.broker = EventBroker()
        self.repository = FakeRepository()
        self.enabled = False
        self.greeting = {
            "enabled": True,
            "trigger_groups": {
                "salutation": ["你好"],
                "availability": ["在吗"],
                "thanks": ["谢谢"],
                "goodbye": ["再见"],
            },
            "reply_templates": {
                "salutation": "您好",
                "availability": "我在",
                "thanks": "不客气",
                "goodbye": "再见",
            },
            "updated_at": None,
        }
        self.handoff = {
            "reply_template": "您好，您反馈的售后问题已为您转接人工客服处理，请您稍候。",
            "updated_at": None,
        }

    async def ensure_initialized(self):
        return None

    async def list_conversations(self, **_kwargs):
        return [{
            "id": "c1", "platform_conversation_id": "p1", "display_name": "顾客",
            "goods_id": None, "goods_name": None, "state": "pending",
            "last_outbound_status": None,
            "auto_reply_enabled": True, "unread_count": 1,
            "last_message_at": NOW, "updated_at": NOW,
        }]

    async def conversation_detail(self, conversation_id, **_kwargs):
        assert conversation_id == "c1"
        return {"conversation": (await self.list_conversations())[0], "messages": [], "decision": None}

    async def mark_conversation_read(self, conversation_id):
        assert conversation_id == "c1"
        item = (await self.list_conversations())[0]
        item["unread_count"] = 0
        return item

    async def clear_conversation_response_timer(self, conversation_id):
        assert conversation_id == "c1"
        item = (await self.list_conversations())[0]
        item["response_started_at"] = None
        item["response_deadline_at"] = None
        return item

    async def reply(self, conversation_id, *, content, client_request_id):
        return {"id": "j1", "conversation_id": conversation_id, "client_request_id": client_request_id,
                "source": "manual", "content": content, "status": "queued", "error_code": None,
                "clicked_at": None, "sent_at": None, "created_at": NOW}

    async def automation(self):
        return {"enabled": self.enabled}

    async def set_automation(self, enabled):
        self.enabled = enabled
        return {"enabled": enabled}

    async def greeting_automation(self):
        return self.greeting

    async def set_greeting_automation(
        self, *, enabled, trigger_groups, reply_templates
    ):
        self.greeting = {
            "enabled": enabled,
            "trigger_groups": trigger_groups,
            "reply_templates": reply_templates,
            "updated_at": NOW,
        }
        return self.greeting

    async def handoff_automation(self):
        return self.handoff

    async def set_handoff_automation(self, reply_template):
        self.handoff = {"reply_template": reply_template, "updated_at": NOW}
        return self.handoff


class FakeShopRuntime:
    async def create_knowledge_gap_qa_draft(self, gap_id, *, standard_answer):
        assert gap_id == "gap-1"
        return {
            "id": gap_id,
            "shop_id": "shop-1",
            "product_id": "sku-1",
            "normalized_question": "怎么使用",
            "example_question": "这个怎么使用？",
            "reason_code": "fallback",
            "occurrences": 2,
            "status": "draft",
            "candidate_answer": standard_answer,
            "linked_qa_code": "KG-gap-1",
            "first_seen_at": NOW,
            "last_seen_at": NOW,
        }


class FakeShopManager:
    async def get_runtime(self, shop_id):
        assert shop_id == "shop-1"
        return FakeShopRuntime()


@pytest.fixture
def fake_runtime():
    runtime = FakeRuntime()
    app.dependency_overrides[get_console_runtime] = lambda: runtime
    yield runtime
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_conversation_pagination_and_idempotency_key_validation(fake_runtime) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get("/api/v1/conversations?limit=20")
        invalid = await client.post("/api/v1/conversations/c1/reply", json={"content": "您好", "client_request_id": "x"})
        valid = await client.post("/api/v1/conversations/c1/reply", json={"content": "您好", "client_request_id": "manual:12345678"})
    assert listing.status_code == 200
    assert listing.json()["data"]["items"][0]["unread_count"] == 1
    assert invalid.status_code == 422
    assert valid.status_code == 202
    assert valid.json()["data"]["status"] == "queued"


@pytest.mark.asyncio
async def test_global_automation_switch(fake_runtime) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/api/v1/automation", json={"enabled": True})
    assert response.json() == {"code": 0, "message": "success", "data": {"enabled": True}}


@pytest.mark.asyncio
async def test_greeting_automation_config_validation_and_update(fake_runtime) -> None:
    payload = {
        "enabled": True,
        "trigger_groups": {
            "salutation": [" 你好 ", "你好", ""],
            "availability": ["在吗"],
            "thanks": ["谢谢"],
            "goodbye": ["再见"],
        },
        "reply_templates": {
            "salutation": " 您好，亲，请问有什么可以帮您？ ",
            "availability": "我在的",
            "thanks": "不客气",
            "goodbye": "祝您愉快",
        },
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.put("/api/v1/automation/greeting", json=payload)
        duplicate = await client.put(
            "/api/v1/automation/greeting",
            json={
                **payload,
                "trigger_groups": {
                    **payload["trigger_groups"],
                    "availability": ["你好", "在吗"],
                },
            },
        )
        current = await client.get("/api/v1/automation/greeting")

    data = created.json()["data"]
    assert data["enabled"] is True
    assert data["trigger_groups"]["salutation"] == ["你好"]
    assert data["reply_templates"]["salutation"] == ["您好，亲，请问有什么可以帮您？"]
    assert duplicate.status_code == 422
    assert current.json()["data"] == data


@pytest.mark.asyncio
async def test_handoff_automation_config_can_be_updated(fake_runtime) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        current = await client.get("/api/v1/automation/handoff")
        updated = await client.put(
            "/api/v1/automation/handoff",
            json={"reply_template": "售后问题已转人工，请稍候。"},
        )
        invalid = await client.put(
            "/api/v1/automation/handoff", json={"reply_template": "   "}
        )
    assert current.status_code == 200
    assert updated.json()["data"]["reply_template"] == "售后问题已转人工，请稍候。"
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_opened_conversation_can_be_marked_read(fake_runtime) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/conversations/c1/read")
    assert response.status_code == 200
    assert response.json()["data"]["unread_count"] == 0


@pytest.mark.asyncio
async def test_conversation_response_timer_can_be_cleared(fake_runtime) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/conversations/c1/response-timer")
    assert response.status_code == 200
    assert response.json()["data"]["response_started_at"] is None
    assert response.json()["data"]["response_deadline_at"] is None


@pytest.mark.asyncio
async def test_sse_replays_from_last_event_id(fake_runtime) -> None:
    response = await events(fake_runtime, last_event_id="4", cursor=None)
    first = await anext(response.body_iterator)
    assert "id: 5" in first
    assert "event: message.created" in first
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_knowledge_gap_can_be_converted_to_safe_qa_draft() -> None:
    app.dependency_overrides[get_shop_runtime_manager] = lambda: FakeShopManager()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/shops/shop-1/knowledge-gaps/gap-1/qa-draft",
                json={"standard_answer": " 请按说明书使用。 "},
            )
    finally:
        app.dependency_overrides.pop(get_shop_runtime_manager, None)

    assert response.status_code == 201
    assert response.json()["data"]["status"] == "draft"
    assert response.json()["data"]["candidate_answer"] == "请按说明书使用。"
