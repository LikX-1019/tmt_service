from unittest.mock import AsyncMock

import pytest

from app.integrations.pdd.base import ConnectorStatus, ConnectorStatusSnapshot
from app.integrations.pdd.playwright_connector import PddPlaywrightConnector
from app.integrations.pdd.selectors import (
    COUNT_OUTBOUND_TEXT_SCRIPT,
    LATEST_OUTBOUND_TEXT_SCRIPT,
    MESSAGE_HISTORY_STATE_SCRIPT,
    SCAN_CONVERSATIONS_SCRIPT,
    SCAN_CURRENT_MESSAGES_SCRIPT,
    SCROLL_MESSAGE_HISTORY_SCRIPT,
)


@pytest.mark.asyncio
async def test_dom_contract_discovers_messages_and_confirms_send() -> None:
    playwright = pytest.importorskip("playwright.async_api")
    manager = await playwright.async_playwright().start()
    try:
        try:
            browser = await manager.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"系统 Chrome 不可用：{exc}")
        page = await browser.new_page()
        await page.set_content(
            """
          <div class="chat-item active" data-random="buyer-1-reply">
            <div class="chat-portrait"><img src="https://img.example/avatar-a.jpg?token=secret"></div>
            <span class="name">顾客甲</span><span class="unread">2</span>
          </div>
          <div class="message-list">
            <div class="message-item" data-message-id="m1"><span currentuid="buyer-1" class="message-text">重力珠在哪里？</span></div>
            <div class="message-item right" data-message-id="m2"><span currentuid="buyer-1" class="message-text">在边缘区域</span></div>
          </div>
          <textarea id="replyTextarea"></textarea><button class="send-btn">发送</button>
        """,
            wait_until="domcontentloaded",
        )
        conversations = await page.evaluate(SCAN_CONVERSATIONS_SCRIPT)
        messages = await page.evaluate(SCAN_CURRENT_MESSAGES_SCRIPT, "buyer-1")
        assert conversations[0].pop("identity_material") == "顾客甲|https://img.example/avatar-a.jpg"
        assert conversations[0] | {"activity_key": None} == {
            "conversation_id": "buyer-1",
            "platform_customer_id": "buyer-1",
            "dom_conversation_id": "buyer-1-reply",
            "display_name": "顾客甲",
            "avatar_url": "https://img.example/avatar-a.jpg?token=secret",
            "unread": 2,
            "index": 0,
            "active": True,
            "activity_key": None,
        }
        assert conversations[0]["avatar_url"] == "https://img.example/avatar-a.jpg?token=secret"
        assert messages[0]["direction"] == "inbound"
        assert messages[1]["direction"] == "outbound"
        assert await page.evaluate(COUNT_OUTBOUND_TEXT_SCRIPT, "在边缘区域") == 1
        latest = await page.evaluate(LATEST_OUTBOUND_TEXT_SCRIPT, "在边缘区域")
        assert latest["message_id"] == "m2"
        await browser.close()
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_send_confirms_service_attitude_reminder() -> None:
    playwright = pytest.importorskip("playwright.async_api")
    manager = await playwright.async_playwright().start()
    try:
        try:
            browser = await manager.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"系统 Chrome 不可用：{exc}")
        page = await browser.new_page()
        await page.set_content("""
          <div class="reply-box">
            <textarea id="replyTextarea"></textarea>
            <button class="send-btn">发送</button>
          </div>
          <div id="reminder" class="el-message-box__wrapper" style="display: none">
            <strong>服务态度提醒</strong>
            <p>您已向当前消费者发送2条相同内容的消息，确认继续发送？</p>
            <div id="continue-send" role="button">继续发送</div>
            <button id="modify">去修改</button>
          </div>
          <div id="messages"></div>
          <script>
            document.querySelector('.send-btn').addEventListener('click', () => {
              document.querySelector('#reminder').style.display = 'block';
            });
            document.querySelector('#continue-send').addEventListener('click', () => {
              document.body.dataset.clicked = 'continue';
              document.querySelector('#reminder').style.display = 'none';
              const message = document.createElement('div');
              message.className = 'message-item right';
              message.dataset.messageId = 'confirmed-message';
              message.textContent = document.querySelector('#replyTextarea').value;
              document.querySelector('#messages').append(message);
            });
            document.querySelector('#modify').addEventListener('click', () => {
              document.body.dataset.clicked = 'modify';
            });
          </script>
        """)

        connector = PddPlaywrightConnector()
        connector._page = page
        connector._snapshot = ConnectorStatusSnapshot(ConnectorStatus.READY)
        connector._open_conversation = AsyncMock()

        receipt = await connector.send_message("buyer-1", "您好，亲，我在的")

        assert receipt.confirmed_at >= receipt.clicked_at
        assert connector._snapshot.status == ConnectorStatus.READY
        assert await page.locator("body").get_attribute("data-clicked") == "continue"
        assert await page.locator("#messages").get_by_text("您好，亲，我在的").count() == 1
        await browser.close()
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_conversation_state_variants_share_one_customer_id() -> None:
    playwright = pytest.importorskip("playwright.async_api")
    manager = await playwright.async_playwright().start()
    try:
        try:
            browser = await manager.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"系统 Chrome 不可用：{exc}")
        page = await browser.new_page()
        await page.set_content("""
          <div class="chat-item-box" data-random="1855811761-0-all">
            <span class="chat-nickname">L*e</span>
          </div>
          <div class="chat-item-box active" data-random="1855811761-0-unTimeout">
            <span class="chat-nickname">L*e</span><span class="unread">1</span>
          </div>
        """)
        conversations = await page.evaluate(SCAN_CONVERSATIONS_SCRIPT)
        assert len(conversations) == 1
        assert conversations[0]["platform_customer_id"] == "1855811761"
        assert conversations[0]["dom_conversation_id"] == "1855811761-0-unTimeout"
        await browser.close()
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_current_pdd_dom_shape_is_supported() -> None:
    playwright = pytest.importorskip("playwright.async_api")
    manager = await playwright.async_playwright().start()
    try:
        try:
            browser = await manager.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"系统 Chrome 不可用：{exc}")
        page = await browser.new_page()
        await page.set_content(
            """
          <div class="chat-list-box"><div class="chat-list"><ul>
            <li class="chat-item"><div class="chat-item-box active" data-random="buyer-2-reply">
              <div class="chat-portrait"><img src="https://img.example/avatar-b.jpg"></div>
              <div class="chat-detail"><span class="chat-nickname">顾客乙</span>
                <p class="bottom-message"><span class="chat-message-content">新消息</span></p>
              </div><span class="chat-status-icon">1</span>
            </div></li>
          </ul></div></div>
          <div class="content-box"><div class="history-box"><ul class="msg-list">
            <li class="clearfix onemsg" id="pdd-message-1"><div class="merchantMessage">
              <p class="message-time">13:50</p><div class="buyer-item">
                <span currentuid="buyer-2"></span>
                <div class="msg-content"><p class="msg-content-box">请问怎么使用？</p></div>
              </div></div></li>
            <li class="clearfix onemsg" id="pdd-message-2"><div class="merchantMessage">
              <div class="cs-item"><div currentuid="buyer-2"><p class="nickname">客服甲</p><div class="msg-content"><p class="msg-content-box">您好</p></div></div></div>
            </div></li>
          </ul></div><div class="reply-box"><div class="reply-input"><textarea id="replyTextarea"></textarea></div>
            <button class="send-btn">发送</button></div></div>
        """,
            wait_until="domcontentloaded",
        )
        conversations = await page.evaluate(SCAN_CONVERSATIONS_SCRIPT)
        messages = await page.evaluate(SCAN_CURRENT_MESSAGES_SCRIPT, "buyer-2")
        history = await page.evaluate(MESSAGE_HISTORY_STATE_SCRIPT)
        assert conversations[0]["conversation_id"] == "buyer-2"
        assert conversations[0]["platform_customer_id"] == "buyer-2"
        assert conversations[0]["display_name"] == "顾客乙"
        assert conversations[0]["avatar_url"] == "https://img.example/avatar-b.jpg"
        assert messages[0]["direction"] == "inbound"
        assert messages[0]["content"] == "请问怎么使用？"
        assert messages[0]["message_id"] == "pdd-message-1"
        assert messages[1]["direction"] == "outbound"
        assert messages[1]["sender_name"] == "客服甲"
        assert history["available"] is True
        assert history["message_count"] == 2
        assert history["customer_ids"] == ["buyer-2"]
        assert await page.evaluate(SCROLL_MESSAGE_HISTORY_SCRIPT, "top") is True
        await browser.close()
    finally:
        await manager.stop()
