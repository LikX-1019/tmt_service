"""客服平台连接器的稳定业务接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import re


def normalize_platform_customer_id(value: object) -> str:
    """把拼多多同一顾客的 all/reply/unTimeout 等列表变体归一为稳定 UID。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    numeric = re.match(r"^(\d+)(?:-|$)", raw)
    if numeric:
        return numeric.group(1)
    return re.sub(r"-(?:all|reply)$", "", raw)


class ConnectorStatus(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    LOGIN_REQUIRED = "login_required"
    READY = "ready"
    DEGRADED = "degraded"
    ERROR = "error"


@dataclass(slots=True)
class BrowserAsset:
    """已由连接器缓存、等待随消息一起落库的媒体资源。"""

    asset_type: str
    source_url: str | None = None
    storage_key: str | None = None
    mime_type: str | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class BrowserMessage:
    """连接器向业务层提交的标准入站消息。"""

    platform_conversation_id: str
    display_name: str
    direction: str
    kind: str
    content: str | None
    occurred_at: datetime
    platform_message_id: str | None = None
    goods_id: str | None = None
    goods_name: str | None = None
    goods_price: str | None = None
    goods_url: str | None = None
    is_backfill: bool = False
    fingerprint: str = ""
    avatar_url: str | None = None
    platform_customer_id: str | None = None
    sender_type: str | None = None
    sender_name: str | None = None
    assets: tuple[BrowserAsset, ...] = ()


@dataclass(slots=True)
class CustomerProfile:
    """从会话列表读取的稳定顾客身份与当前展示资料。"""

    platform_customer_id: str
    display_name: str
    avatar_url: str | None = None


@dataclass(slots=True)
class ConnectorStatusSnapshot:
    status: ConnectorStatus
    detail: str | None = None
    changed_at: datetime | None = None


@dataclass(slots=True)
class SendReceipt:
    clicked_at: datetime
    confirmed_at: datetime
    platform_message_id: str | None = None
    responder_name: str | None = None


class ConnectorError(RuntimeError):
    """发送点击前或连接阶段的确定性失败。"""


class ConversationNotVisibleError(ConnectorError):
    """会话列表刷新后目标暂时离开可见区域，可在下一轮重新发现。"""


class SendUncertainError(ConnectorError):
    """点击后无法确认结果，调用方不得自动重试。"""

    def __init__(self, message: str, *, clicked_at: datetime) -> None:
        self.clicked_at = clicked_at
        super().__init__(message)


MessageCallback = Callable[[BrowserMessage], Awaitable[None]]
ProfileCallback = Callable[[CustomerProfile], Awaitable[None]]
StatusCallback = Callable[[ConnectorStatusSnapshot], Awaitable[None]]


class CustomerServiceConnector(ABC):
    """网页自动化与未来官方接口共同实现的抽象边界。"""

    @abstractmethod
    async def start(
        self,
        on_message: MessageCallback,
        on_status: StatusCallback,
        on_profile: ProfileCallback | None = None,
    ) -> ConnectorStatusSnapshot:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> ConnectorStatusSnapshot:
        raise NotImplementedError

    @abstractmethod
    async def status(self) -> ConnectorStatusSnapshot:
        raise NotImplementedError

    @abstractmethod
    async def send_message(
        self, platform_conversation_id: str, content: str
    ) -> SendReceipt:
        raise NotImplementedError

    async def diagnostics(self) -> dict[str, object]:
        """返回不含正文、Cookie 和属性值的结构诊断。"""
        snapshot = await self.status()
        return {"status": snapshot.status.value, "frames": []}
