from unittest.mock import AsyncMock, Mock

import pytest

from app.integrations.pdd import playwright_connector as playwright_connector_module
from app.integrations.pdd.base import ConnectorError, ConnectorStatus
from app.integrations.pdd.playwright_connector import (
    PddPlaywrightConnector,
    _message_backfill_flags,
)
from app.integrations.pdd.selectors import DOM_CONTRACT_SCRIPT, SCAN_CONVERSATIONS_SCRIPT


def test_startup_scan_marks_every_message_as_backfill() -> None:
    messages = [
        {"direction": "inbound", "content": "你好"},
        {"direction": "outbound", "content": "您好"},
    ]

    assert _message_backfill_flags(
        messages,
        startup_backfill=True,
        already_synced=False,
        unread=1,
    ) == [True, True]


def test_first_sync_only_marks_latest_unread_inbound_messages_as_live() -> None:
    messages = [
        {"direction": "inbound", "content": "历史问题"},
        {"direction": "outbound", "content": "历史回复"},
        {"direction": "inbound", "content": "你好"},
        {"direction": "inbound", "content": "在吗"},
    ]

    assert _message_backfill_flags(
        messages,
        startup_backfill=False,
        already_synced=False,
        unread=2,
    ) == [True, True, False, False]


def test_first_sync_without_unread_messages_remains_backfill() -> None:
    messages = [{"direction": "inbound", "content": "历史问题"}]

    assert _message_backfill_flags(
        messages,
        startup_backfill=False,
        already_synced=False,
        unread=0,
    ) == [True]


def test_synced_conversation_treats_newly_discovered_messages_as_live() -> None:
    messages = [
        {"direction": "inbound", "content": "新问题"},
        {"direction": "outbound", "content": "人工回复"},
    ]

    assert _message_backfill_flags(
        messages,
        startup_backfill=False,
        already_synced=True,
        unread=0,
    ) == [False, False]


@pytest.mark.asyncio
async def test_diagnostics_reports_standalone_browser_mode() -> None:
    class Locator:
        async def count(self) -> int:
            return 0

    class Page:
        frames = []

        def is_closed(self) -> bool:
            return False

        def locator(self, _selector: str) -> Locator:
            return Locator()

    connector = PddPlaywrightConnector()
    connector._page = Page()

    diagnostics = await connector.diagnostics()

    assert diagnostics["browser_mode"] == "standalone"
    assert diagnostics["native_reply_input_count"] == 0
    assert diagnostics["native_input_operable"] is False
    assert "workbench" not in diagnostics


@pytest.mark.asyncio
async def test_scan_continues_after_one_conversation_fails() -> None:
    class Page:
        def is_closed(self) -> bool:
            return False

        async def evaluate(self, script):
            if script == SCAN_CONVERSATIONS_SCRIPT:
                return [
                    {"platform_customer_id": "manual-buyer"},
                    {"platform_customer_id": "auto-buyer"},
                ]
            if script == DOM_CONTRACT_SCRIPT:
                return {"conversation_root": True}
            raise AssertionError("unexpected script")

    connector = PddPlaywrightConnector()
    connector._page = Page()
    connector._is_login_required = AsyncMock(return_value=False)
    connector._scan_conversation = AsyncMock(
        side_effect=[ConnectorError("当前会话不可操作"), None]
    )
    connector._set_status = AsyncMock()

    await connector._scan_once()

    assert connector._scan_conversation.await_count == 2
    connector._set_status.assert_awaited_once_with(
        ConnectorStatus.READY, "消息监听正常，1 个会话等待重试"
    )


@pytest.mark.asyncio
async def test_start_passes_shop_id_to_poll_task(monkeypatch, tmp_path) -> None:
    """回归：start() 曾引用未定义的 _shop_id，导致专用浏览器永远无法启动。"""

    class FakePage:
        frames: list[object] = []

        def is_closed(self) -> bool:
            return False

        async def expose_function(self, _name, _handler) -> None:
            return None

        async def add_init_script(self, _script) -> None:
            return None

        def on(self, _event, _handler) -> None:
            return None

        async def goto(self, _url, **_kwargs) -> None:
            return None

    class FakeContext:
        def __init__(self) -> None:
            self.pages = [FakePage()]

    class FakeChromium:
        async def launch_persistent_context(self, **_kwargs) -> FakeContext:
            return FakeContext()

    class FakeBrowserType:
        chromium = FakeChromium()

    class FakePlaywrightManager:
        async def start(self) -> FakeBrowserType:
            return FakeBrowserType()

    monkeypatch.setattr(
        "playwright.async_api.async_playwright", lambda: FakePlaywrightManager()
    )
    captured: dict[str, object] = {}

    def fake_create_log_task(coroutine, **kwargs):
        # 真实实现由事件循环接管协程；测试中显式关闭，避免未 await 告警。
        coroutine.close()
        captured.update(kwargs)
        return Mock()

    monkeypatch.setattr(
        playwright_connector_module, "create_log_task", fake_create_log_task
    )

    connector = PddPlaywrightConnector(
        shop_id="shop-under-test", profile_dir=tmp_path / "profile"
    )
    connector._is_login_required = AsyncMock(return_value=True)
    connector._scan_once = AsyncMock(return_value=None)

    await connector.start(on_message=AsyncMock(), on_status=AsyncMock())

    assert captured["shop_id"] == "shop-under-test"
    assert captured["name"] == "pdd-dom-poller"
