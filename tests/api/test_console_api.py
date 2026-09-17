from datetime import datetime, timezone

import httpx
import pytest

from app.api.dependencies import get_console_runtime
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
async def test_opened_conversation_can_be_marked_read(fake_runtime) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/conversations/c1/read")
    assert response.status_code == 200
    assert response.json()["data"]["unread_count"] == 0


@pytest.mark.asyncio
async def test_sse_replays_from_last_event_id(fake_runtime) -> None:
    response = await events(fake_runtime, last_event_id="4", cursor=None)
    first = await anext(response.body_iterator)
    assert "id: 5" in first
    assert "event: message.created" in first
    await response.body_iterator.aclose()
