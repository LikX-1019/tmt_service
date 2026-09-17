"""使用独立 Chrome 会话驱动拼多多网页客服。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.integrations.pdd.base import (
    ConnectorError,
    ConversationNotVisibleError,
    ConnectorStatus,
    ConnectorStatusSnapshot,
    CustomerProfile,
    CustomerServiceConnector,
    MessageCallback,
    ProfileCallback,
    SendReceipt,
    SendUncertainError,
    StatusCallback,
    normalize_platform_customer_id,
)
from app.services.message_asset_storage import MessageAssetStorage, UnsupportedAssetError
from app.integrations.pdd.parser import parse_browser_message
from app.integrations.pdd.workbench import build_workbench_init_script
from app.integrations.pdd.selectors import (
    CONVERSATION_ITEM_SELECTORS,
    COUNT_OUTBOUND_TEXT_SCRIPT,
    DOM_CONTRACT_SCRIPT,
    DOM_DIAGNOSTICS_SCRIPT,
    LATEST_OUTBOUND_TEXT_SCRIPT,
    MESSAGE_HISTORY_STATE_SCRIPT,
    MUTATION_OBSERVER_SCRIPT,
    REPLY_TEXTAREA_SELECTORS,
    SCAN_CONVERSATIONS_SCRIPT,
    SCAN_CURRENT_MESSAGES_SCRIPT,
    SCROLL_MESSAGE_HISTORY_SCRIPT,
    SEND_BUTTON_SELECTORS,
)


logger = logging.getLogger(__name__)


def _message_backfill_flags(
    raw_messages: list[dict[str, Any]],
    *,
    startup_backfill: bool,
    already_synced: bool,
    unread: int,
) -> list[bool]:
    """区分首次同步的历史消息与真正未读的新入站消息。"""
    if startup_backfill:
        return [True] * len(raw_messages)
    if already_synced:
        return [False] * len(raw_messages)
    if unread <= 0:
        return [True] * len(raw_messages)

    live_indexes: set[int] = set()
    for index in range(len(raw_messages) - 1, -1, -1):
        direction = str(raw_messages[index].get("direction") or "inbound").lower()
        if direction != "inbound":
            continue
        live_indexes.add(index)
        if len(live_indexes) >= unread:
            break
    return [index not in live_indexes for index in range(len(raw_messages))]


class PddPlaywrightConnector(CustomerServiceConnector):
    """只通过可见 DOM 收取和发送消息，不调用平台未公开接口。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._snapshot = ConnectorStatusSnapshot(ConnectorStatus.STOPPED)
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._poll_task: asyncio.Task[None] | None = None
        self._on_message: MessageCallback | None = None
        self._on_profile: ProfileCallback | None = None
        self._on_status: StatusCallback | None = None
        self._send_lock = asyncio.Lock()
        self._page_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._seen: set[str] = set()
        self._conversation_activity: dict[str, str] = {}
        self._conversation_profiles: dict[str, tuple[str, str | None]] = {}
        self._platform_to_conversation: dict[str, str] = {}
        self._synced_conversations: set[str] = set()
        self._initial_scan_complete = False
        self._backfill_until = 0.0
        self._stopping = False
        self._asset_storage = MessageAssetStorage(
            self._settings.message_asset_dir,
            max_bytes=self._settings.message_asset_max_bytes,
        )

    async def status(self) -> ConnectorStatusSnapshot:
        return self._snapshot

    async def diagnostics(self) -> dict[str, object]:
        """仅收集页面结构特征，严禁返回正文或 data 属性值。"""
        frames: list[dict[str, object]] = []
        workbench: dict[str, object] = {
            "mounted": False,
            "resize_active": False,
            "shield_active": False,
            "left_panel_count": 0,
            "right_panel_count": 0,
        }
        if self._page and not self._page.is_closed():
            try:
                workbench_result = await self._page.evaluate(
                    """
                    () => {
                      const host = document.getElementById('tmt-workbench-host');
                      const root = host?.shadowRoot;
                      const shield = root?.querySelector('.shield');
                      return {
                        mounted: Boolean(host && root),
                        resize_active: host?.dataset.resizeActive === 'true',
                        shield_active: Boolean(shield?.classList.contains('active')),
                        left_panel_count: root?.querySelectorAll('.pane.left').length || 0,
                        right_panel_count: root?.querySelectorAll('.pane.right').length || 0,
                      };
                    }
                    """
                )
                if isinstance(workbench_result, dict):
                    workbench.update(workbench_result)
            except Exception:
                logger.exception(
                    "pdd_workbench_diagnostics_failed",
                    extra={"event": "pdd_workbench_diagnostics_failed"},
                )
            for index, frame in enumerate(self._page.frames):
                try:
                    item = await frame.evaluate(DOM_DIAGNOSTICS_SCRIPT)
                except Exception:
                    continue
                if isinstance(item, dict):
                    if index == 0:
                        raw_conversations = await frame.evaluate(
                            SCAN_CONVERSATIONS_SCRIPT
                        )
                        if isinstance(raw_conversations, list):
                            item["scan_summary"] = [
                                {
                                    "id_hash": sha256(
                                        str(entry.get("conversation_id") or "").encode()
                                    ).hexdigest()[:12],
                                    "unread": int(entry.get("unread") or 0),
                                    "active": bool(entry.get("active")),
                                }
                                for entry in raw_conversations
                                if isinstance(entry, dict)
                            ]
                    frames.append({"index": index, **item})
        console_host = self._settings.app_host
        if console_host in {"0.0.0.0", "::"}:
            console_host = "127.0.0.1"
        console_origin = f"http://{console_host}:{self._settings.app_port}"
        console_reply_count = sum(
            int(item.get("current_reply_matches") or 0)
            for item in frames
            if str(item.get("url") or "").startswith(console_origin)
        )
        native_reply_count = sum(
            int(item.get("current_reply_matches") or 0)
            for item in frames
            if not str(item.get("url") or "").startswith(console_origin)
        )
        workbench["console_reply_input_count"] = console_reply_count
        workbench["native_reply_input_count"] = native_reply_count
        workbench["console_input_operable"] = bool(
            console_reply_count and not workbench["shield_active"]
        )
        workbench["native_input_operable"] = bool(
            native_reply_count and not workbench["shield_active"]
        )
        return {
            "status": self._snapshot.status.value,
            "frames": frames,
            "workbench": workbench,
            "activity_count": len(self._conversation_activity),
            "synced_conversation_count": len(self._synced_conversations),
            "seen_message_count": len(self._seen),
            "initial_scan_complete": self._initial_scan_complete,
            "startup_backfill_active": time.monotonic() < self._backfill_until,
            "send_selector_counts": {
                selector: await self._page.locator(selector).count()
                for selector in SEND_BUTTON_SELECTORS
            }
            if self._page and not self._page.is_closed()
            else {},
        }

    async def _set_status(
        self, status: ConnectorStatus, detail: str | None = None
    ) -> ConnectorStatusSnapshot:
        changed = status != self._snapshot.status or detail != self._snapshot.detail
        self._snapshot = ConnectorStatusSnapshot(
            status=status,
            detail=detail,
            changed_at=datetime.now(timezone.utc),
        )
        if changed and self._on_status:
            await self._on_status(self._snapshot)
        return self._snapshot

    async def start(
        self,
        on_message: MessageCallback,
        on_status: StatusCallback,
        on_profile: ProfileCallback | None = None,
    ) -> ConnectorStatusSnapshot:
        if self._snapshot.status not in {ConnectorStatus.STOPPED, ConnectorStatus.ERROR}:
            return self._snapshot
        self._on_message = on_message
        self._on_profile = on_profile
        self._on_status = on_status
        self._stopping = False
        self._seen.clear()
        self._conversation_activity.clear()
        self._conversation_profiles.clear()
        self._platform_to_conversation.clear()
        self._synced_conversations.clear()
        self._initial_scan_complete = False
        # 页面列表和消息区是分阶段异步渲染的。启动后短暂出现的历史消息
        # 一律视为补录，避免空列表先完成扫描后把旧消息误判为实时入站。
        self._backfill_until = time.monotonic() + 10.0
        await self._set_status(ConnectorStatus.STARTING, "正在启动专用 Chrome")
        try:
            from playwright.async_api import async_playwright

            profile = Path(self._settings.pdd_chrome_profile_dir)
            profile.mkdir(parents=True, exist_ok=True)
            self._playwright = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": str(profile),
                "headless": False,
                # macOS 上 --start-maximized 不可靠，会话恢复可能得到极小窗口；
                # 用显式位置和尺寸保证专用窗口始终铺满主屏工作区。
                "args": ["--window-position=0,30", "--window-size=1920,969"],
                "no_viewport": True,
            }
            if self._settings.pdd_chrome_executable:
                launch_kwargs["executable_path"] = str(
                    self._settings.pdd_chrome_executable
                )
            else:
                launch_kwargs["channel"] = "chrome"
            self._context = await self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await self._page.expose_function("__codexPddWake", self._wake.set)
            await self._page.add_init_script(f"({MUTATION_OBSERVER_SCRIPT})()")
            console_host = self._settings.app_host
            if console_host in {"0.0.0.0", "::"}:
                console_host = "127.0.0.1"
            console_origin = f"http://{console_host}:{self._settings.app_port}"
            await self._page.add_init_script(
                build_workbench_init_script(console_origin)
            )
            self._page.on("websocket", self._observe_websocket)
            self._page.on("crash", lambda: self._schedule_browser_error("页面已崩溃"))
            self._page.on("close", lambda: self._schedule_browser_error("页面已关闭"))
            await self._page.goto(self._settings.pdd_chat_url, wait_until="domcontentloaded")
            if not await self._is_login_required():
                await self._page.wait_for_function(
                    "() => Boolean(document.querySelector('.chat-list-box, .chat-list'))",
                    timeout=15000,
                )
            await self._scan_once(initial=True)
            self._poll_task = asyncio.create_task(self._poll_loop(), name="pdd-dom-poller")
            return self._snapshot
        except Exception as exc:
            logger.exception("pdd_connector_start_failed", extra={"event": "pdd_connector_start_failed"})
            await self._set_status(ConnectorStatus.ERROR, "专用浏览器启动失败")
            await self._close_browser()
            raise ConnectorError("专用浏览器启动失败") from exc

    def _observe_websocket(self, websocket: Any) -> None:
        """WebSocket 只作为重新扫描信号，不读取或解析私有帧内容。"""
        websocket.on("framereceived", lambda _payload: self._wake.set())

    def _schedule_browser_error(self, detail: str) -> None:
        if not self._stopping:
            asyncio.create_task(self._set_status(ConnectorStatus.ERROR, detail))

    async def _is_login_required(self) -> bool:
        if not self._page or self._page.is_closed():
            return False
        url = self._page.url.lower()
        if "login" in url:
            return True
        return await self._page.locator("text=/扫码登录|账号登录|请登录/").count() > 0

    async def _scan_once(self, *, initial: bool = False) -> None:
        if not self._page or self._page.is_closed():
            raise ConnectorError("专用浏览器已关闭")
        if await self._is_login_required():
            await self._set_status(ConnectorStatus.LOGIN_REQUIRED, "请在专用 Chrome 中登录")
            return
        conversations = await self._page.evaluate(SCAN_CONVERSATIONS_SCRIPT)
        if not isinstance(conversations, list):
            raise ConnectorError("会话列表 DOM 契约无效")
        contract = await self._page.evaluate(DOM_CONTRACT_SCRIPT)
        if not isinstance(contract, dict) or not contract.get("conversation_root"):
            raise ConnectorError("客服页面会话列表不可用")
        startup_backfill = (
            not self._initial_scan_complete or time.monotonic() < self._backfill_until
        )
        skipped = 0
        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            try:
                await self._scan_conversation(
                    conversation, startup_backfill=startup_backfill
                )
            except ConversationNotVisibleError:
                # 拼多多会在接待状态变化时重排列表；下一轮重新扫描即可。
                skipped += 1
            except Exception as exc:
                skipped += 1
                customer_id = normalize_platform_customer_id(
                    conversation.get("platform_customer_id")
                    or conversation.get("conversation_id")
                    or ""
                )
                logger.exception(
                    "pdd_conversation_scan_skipped",
                    extra={
                        "event": "pdd_conversation_scan_skipped",
                        "customer_id_hash": sha256(customer_id.encode()).hexdigest()[:12]
                        if customer_id
                        else None,
                        "error": str(exc)[:200],
                    },
                )
        self._initial_scan_complete = True
        detail = "消息监听正常"
        if skipped:
            detail = f"消息监听正常，{skipped} 个会话等待重试"
        await self._set_status(ConnectorStatus.READY, detail)

    async def _scan_conversation(
        self, conversation: dict[str, Any], *, startup_backfill: bool
    ) -> None:
        if conversation.get("contract_error"):
            raise ConnectorError("会话选择器结果缺少唯一标识")
        platform_customer_id = normalize_platform_customer_id(
            conversation.get("platform_customer_id")
            or conversation.get("conversation_id")
            or ""
        )
        if not platform_customer_id:
            raise ConnectorError("会话选择器结果缺少平台会话标识")
        conversation["conversation_id"] = platform_customer_id
        unread = int(conversation.get("unread") or 0)
        conversation_id = platform_customer_id
        dom_conversation_id = str(
            conversation.get("dom_conversation_id") or platform_customer_id
        )
        self._platform_to_conversation[dom_conversation_id] = conversation_id
        profile = (
            str(conversation.get("display_name") or "顾客"),
            str(conversation.get("avatar_url") or "") or None,
        )
        profile_changed = self._conversation_profiles.get(conversation_id) != profile
        self._conversation_profiles[conversation_id] = profile
        if self._on_profile and profile_changed:
            await self._on_profile(
                CustomerProfile(
                    platform_customer_id=platform_customer_id,
                    display_name=profile[0],
                    avatar_url=profile[1],
                )
            )
        activity_key = str(conversation.get("activity_key") or "")
        activity_changed = self._conversation_activity.get(conversation_id) != activity_key
        self._conversation_activity[conversation_id] = activity_key
        if (
            self._initial_scan_complete
            and unread <= 0
            and not conversation.get("active")
            and not activity_changed
            and conversation_id in self._synced_conversations
        ):
            return
        await self._open_conversation(conversation_id)
        panel_contract = await self._page.evaluate(DOM_CONTRACT_SCRIPT)
        if not isinstance(panel_contract, dict) or not panel_contract.get("message_root"):
            raise ConnectorError("客服会话消息区不可用")
        already_synced = conversation_id in self._synced_conversations
        raw_messages = await self._collect_current_messages(
            conversation_id,
            include_history=not already_synced,
        )
        if not isinstance(raw_messages, list):
            raw_messages = []
        backfill_flags = _message_backfill_flags(
            raw_messages,
            startup_backfill=startup_backfill,
            already_synced=already_synced,
            unread=unread,
        )
        for raw, is_backfill in zip(raw_messages, backfill_flags, strict=True):
            if not isinstance(raw, dict):
                continue
            raw["display_name"] = conversation.get("display_name") or "顾客"
            raw["avatar_url"] = conversation.get("avatar_url")
            await self._materialize_assets(raw)
            message = parse_browser_message(raw, is_backfill=is_backfill)
            if message.fingerprint in self._seen:
                continue
            self._seen.add(message.fingerprint)
            if self._on_message:
                try:
                    await self._on_message(message)
                except Exception:
                    self._seen.discard(message.fingerprint)
                    raise
        self._synced_conversations.add(conversation_id)

    async def _materialize_assets(self, raw: dict[str, Any]) -> None:
        """使用已登录浏览器下载媒体；失败时截取单条消息节点作为兜底。"""
        candidates = raw.get("assets")
        if not isinstance(candidates, list):
            candidates = []
        materialized: list[dict[str, Any]] = []
        request = getattr(self._context, "request", None)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            item = dict(candidate)
            source_url = str(item.get("source_url") or "").strip()
            try:
                if not request or not source_url:
                    raise UnsupportedAssetError("资源地址不可下载")
                response = await request.get(source_url, timeout=10_000)
                if not response.ok:
                    raise UnsupportedAssetError(f"媒体下载失败: {response.status}")
                stored = self._asset_storage.store_bytes(await response.body())
                item.update({"storage_key": stored.storage_key, "mime_type": stored.mime_type, "sha256": stored.sha256})
            except Exception as exc:
                logger.warning("pdd_asset_download_failed", extra={"event": "pdd_asset_download_failed", "error": str(exc)[:200]})
                continue
            materialized.append(item)
        if materialized:
            raw["assets"] = materialized
            if raw.get("kind") in {"image", "emoji", "unsupported"}:
                raw["kind"] = "emoji" if all(item.get("asset_type") == "emoji" for item in materialized) else "image"
            return
        # 方案三兜底：只对无法下载的特殊消息截取当前消息节点，不截取整个页面。
        if raw.get("kind") not in {"unsupported", "image", "emoji", "goods_card"} or not self._page:
            return
        dom_index = raw.get("dom_index")
        try:
            if isinstance(dom_index, int):
                nodes = self._page.locator('.history-box .onemsg, .msg-list .onemsg, [data-message-id], [data-msg-id], .message-item, [class*="message-row"]')
                if dom_index < await nodes.count():
                    payload = await nodes.nth(dom_index).screenshot(type="png")
                    stored = self._asset_storage.store_bytes(payload)
                    raw["assets"] = [{"asset_type": "screenshot", "storage_key": stored.storage_key, "mime_type": stored.mime_type, "sha256": stored.sha256, "metadata": {"fallback": True}}]
        except Exception as exc:
            logger.warning("pdd_message_screenshot_failed", extra={"event": "pdd_message_screenshot_failed", "error": str(exc)[:200]})

    async def _collect_current_messages(
        self, conversation_id: str, *, include_history: bool
    ) -> list[dict[str, Any]]:
        """合并聊天区懒加载分段，并在结束时回到底部接收最新消息。"""
        collected: list[dict[str, Any]] = []

        async def collect_visible() -> None:
            state = await self._page.evaluate(MESSAGE_HISTORY_STATE_SCRIPT)
            customer_ids = {
                str(item)
                for item in (state.get("customer_ids") if isinstance(state, dict) else [])
                if item
            }
            if customer_ids and customer_ids != {conversation_id}:
                raise ConnectorError("消息区顾客标识与目标会话不一致")
            batch = await self._page.evaluate(
                SCAN_CURRENT_MESSAGES_SCRIPT, conversation_id
            )
            if isinstance(batch, list):
                for item in batch:
                    if not isinstance(item, dict):
                        continue
                    message_customer_id = str(
                        item.get("platform_customer_id") or conversation_id
                    )
                    if message_customer_id != conversation_id:
                        raise ConnectorError("消息节点归属与目标顾客不一致")
                    collected.append(item)

        await collect_visible()
        if include_history:
            previous_signature: tuple[object, ...] | None = None
            stable_rounds = 0
            for _ in range(10):
                state = await self._page.evaluate(MESSAGE_HISTORY_STATE_SCRIPT)
                if not isinstance(state, dict) or not state.get("available"):
                    break
                await self._page.evaluate(SCROLL_MESSAGE_HISTORY_SCRIPT, "top")
                await asyncio.sleep(0.4)
                await collect_visible()
                current = await self._page.evaluate(MESSAGE_HISTORY_STATE_SCRIPT)
                if not isinstance(current, dict):
                    break
                signature = (
                    current.get("scroll_height"),
                    current.get("message_count"),
                    current.get("first_key"),
                )
                stable_rounds = stable_rounds + 1 if signature == previous_signature else 0
                previous_signature = signature
                if stable_rounds >= 2 or len(collected) >= 600:
                    break
        await self._page.evaluate(SCROLL_MESSAGE_HISTORY_SCRIPT, "bottom")
        await asyncio.sleep(0.2)
        await collect_visible()

        unique: dict[str, dict[str, Any]] = {}
        for item in collected:
            key = str(
                item.get("message_id")
                or "|".join(
                    str(item.get(field) or "")
                    for field in ("direction", "kind", "content", "timestamp")
                )
            )
            unique[key] = item
        return list(unique.values())

    async def _open_conversation(self, conversation_id: str) -> None:
        await self._dismiss_blocking_dialog()
        target = None
        for selector in CONVERSATION_ITEM_SELECTORS:
            items = self._page.locator(selector)
            for index in range(await items.count()):
                item = items.nth(index)
                if not await item.is_visible():
                    continue
                platform_customer_id = await item.evaluate(
                    """item => {
                      const rawId = item.dataset.uid || item.dataset.random || '';
                      const match = rawId.match(/^(\\d+)(?:-|$)/);
                      return match ? match[1] : rawId.replace(/-(?:all|reply)$/, '');
                    }"""
                )
                if str(platform_customer_id) == conversation_id:
                    target = item
                    break
            if target is not None:
                break
        if target is None:
            raise ConversationNotVisibleError("目标会话已不在当前列表")
        await target.click(timeout=3000)
        deadline = time.monotonic() + 6.0
        empty_stable_rounds = 0
        previous_empty_signature: tuple[object, ...] | None = None
        while time.monotonic() < deadline:
            state = await self._page.evaluate(MESSAGE_HISTORY_STATE_SCRIPT)
            customer_ids = {
                str(item)
                for item in (state.get("customer_ids") if isinstance(state, dict) else [])
                if item
            }
            if customer_ids == {conversation_id}:
                break
            if isinstance(state, dict) and not customer_ids and not state.get("message_count"):
                signature = (state.get("first_key"), state.get("last_key"), state.get("message_count"))
                empty_stable_rounds = empty_stable_rounds + 1 if signature == previous_empty_signature else 0
                previous_empty_signature = signature
                if empty_stable_rounds >= 2:
                    break
            await asyncio.sleep(0.1)
        else:
            raise ConnectorError("目标会话点击后消息区顾客标识未切换")
        try:
            await self._page.wait_for_function(
                "() => Boolean(document.querySelector('.history-box, .msg-list, .content-box')) "
                "&& Boolean(document.querySelector('.reply-box, .reply-input'))",
                timeout=3000,
            )
        except Exception as exc:
            raise ConnectorError("客服会话打开后消息区未就绪") from exc
        await asyncio.sleep(0.3)

    async def _dismiss_blocking_dialog(self) -> None:
        """只关闭唯一、可识别的标准弹窗，其他覆盖层保持失败即停。"""
        if await self._confirm_duplicate_message_reminder():
            return
        dialogs = self._page.locator(".el-dialog__wrapper:visible")
        dialog_count = await dialogs.count()
        if dialog_count == 0:
            return
        close_buttons = self._page.locator(
            ".el-dialog__wrapper:visible .el-dialog__headerbtn:visible"
        )
        if dialog_count != 1 or await close_buttons.count() != 1:
            raise ConnectorError("客服页面存在无法安全关闭的阻挡弹窗")
        try:
            await close_buttons.click(timeout=2000)
            await dialogs.wait_for(state="hidden", timeout=2000)
        except Exception as exc:
            raise ConnectorError("客服页面阻挡弹窗关闭失败") from exc

    async def _poll_loop(self) -> None:
        failures = 0
        while not self._stopping:
            try:
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=self._settings.pdd_poll_interval_seconds
                    )
                except TimeoutError:
                    pass
                self._wake.clear()
                async with self._page_lock:
                    await self._scan_once()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                logger.exception("pdd_connector_poll_failed", extra={"event": "pdd_connector_poll_failed"})
                await self._set_status(
                    ConnectorStatus.DEGRADED, "页面结构异常，自动回复已暂停"
                )
                await asyncio.sleep(min(failures, 5))

    async def _first_locator(self, selectors: tuple[str, ...]) -> Any:
        for selector in selectors:
            locator = self._page.locator(selector)
            if await locator.count() == 1:
                return locator
            if await locator.count() > 1:
                raise ConnectorError("页面关键控件不唯一")
        raise ConnectorError("页面关键控件不可用")

    async def _confirm_duplicate_message_reminder(self) -> bool:
        reminders = self._page.locator(
            ".goodsDescRemindPopover:visible, "
            ".el-message-box__wrapper:visible, "
            ".el-dialog__wrapper:visible, "
            ".el-popover:visible, "
            "[role='dialog']:visible"
        ).filter(has_text="相同内容").filter(has_text="继续发送")
        if await reminders.count() == 0:
            return False
        buttons = reminders.locator(
            "button:visible, [role='button']:visible, .el-button:visible"
        ).filter(has_text="继续发送")
        if await buttons.count() == 0:
            buttons = reminders.get_by_text("继续发送", exact=True)
        if await buttons.count() == 0:
            return False
        await buttons.first.click(timeout=2000)
        logger.info(
            "pdd_duplicate_message_reminder_confirmed",
            extra={"event": "pdd_duplicate_message_reminder_confirmed"},
        )
        return True

    async def _wait_for_send_confirmation(
        self, content: str, previous_count: int
    ) -> None:
        """等待发送回显，并自动确认拼多多的重复内容提醒。"""
        deadline = time.monotonic() + 8.0
        reminder_confirmed = False

        while time.monotonic() < deadline:
            current_count = await self._page.evaluate(
                COUNT_OUTBOUND_TEXT_SCRIPT, content
            )
            if int(current_count or 0) > previous_count:
                return

            if not reminder_confirmed:
                reminder_confirmed = await self._confirm_duplicate_message_reminder()
            await asyncio.sleep(0.05)

        raise TimeoutError("页面未出现发送回显")

    async def send_message(
        self, platform_conversation_id: str, content: str
    ) -> SendReceipt:
        if not content.strip() or len(content.strip()) > 400:
            raise ConnectorError("回复内容长度必须为 1 到 400 个字符")
        if self._snapshot.status != ConnectorStatus.READY:
            raise ConnectorError("连接器未就绪")
        async with self._send_lock, self._page_lock:
            content = content.strip()
            try:
                await self._open_conversation(platform_conversation_id)
                textarea = await self._first_locator(REPLY_TEXTAREA_SELECTORS)
                button = await self._first_locator(SEND_BUTTON_SELECTORS)
                before = await self._page.evaluate(COUNT_OUTBOUND_TEXT_SCRIPT, content)
                await textarea.fill(content)
            except Exception as exc:
                if isinstance(exc, ConnectorError):
                    raise
                raise ConnectorError("发送前页面操作失败") from exc
            clicked_at = datetime.now(timezone.utc)
            try:
                await button.click()
                await self._wait_for_send_confirmation(content, before)
            except Exception as exc:
                raise SendUncertainError(
                    "发送点击结果无法确认", clicked_at=clicked_at
                ) from exc
            responder_name = None
            try:
                responder_name = await self._persist_confirmed_outbound(
                    platform_conversation_id, content, clicked_at
                )
            except Exception:
                # 已确认发送不能改判为失败；下一轮 DOM 扫描仍会尝试补录。
                logger.exception(
                    "outbound_message_persist_failed",
                    extra={"event": "outbound_message_persist_failed"},
                )
            return SendReceipt(
                clicked_at=clicked_at,
                confirmed_at=datetime.now(timezone.utc),
                responder_name=responder_name,
            )

    async def _persist_confirmed_outbound(
        self, platform_conversation_id: str, content: str, clicked_at: datetime
    ) -> str | None:
        raw = await self._page.evaluate(LATEST_OUTBOUND_TEXT_SCRIPT, content)
        if not isinstance(raw, dict) or not self._on_message:
            return None
        confirmed_customer_id = str(raw.get("platform_customer_id") or "")
        if confirmed_customer_id and confirmed_customer_id != platform_conversation_id:
            raise ConnectorError("发送回显归属与目标顾客不一致")
        business_conversation_id = self._platform_to_conversation.get(
            platform_conversation_id, platform_conversation_id
        )
        profile = self._conversation_profiles.get(business_conversation_id, ("顾客", None))
        raw.update(
            {
                "conversation_id": business_conversation_id,
                "display_name": profile[0],
                "avatar_url": profile[1],
                "direction": "outbound",
                "kind": "text",
            }
        )
        message = parse_browser_message(raw, observed_at=clicked_at)
        if message.fingerprint in self._seen:
            return message.sender_name
        self._seen.add(message.fingerprint)
        try:
            await self._on_message(message)
        except Exception:
            self._seen.discard(message.fingerprint)
            raise
        return message.sender_name

    async def _close_browser(self) -> None:
        if self._context:
            try:
                await self._context.close()
            except Exception:
                logger.exception("pdd_browser_close_failed", extra={"event": "pdd_browser_close_failed"})
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                logger.exception("playwright_stop_failed", extra={"event": "playwright_stop_failed"})
        self._context = None
        self._page = None
        self._playwright = None

    async def stop(self) -> ConnectorStatusSnapshot:
        self._stopping = True
        if self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None
        await self._close_browser()
        return await self._set_status(ConnectorStatus.STOPPED, "连接器已停止")
