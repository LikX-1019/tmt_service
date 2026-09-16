"""将页面抽取结果规范化，并生成不依赖顾客明文的稳定指纹。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from app.integrations.pdd.base import (
    BrowserAsset,
    BrowserMessage,
    normalize_platform_customer_id,
)


def _parse_datetime(value: Any, observed_at: datetime) -> datetime:
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return observed_at
    if isinstance(value, str) and value.strip():
        candidate = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except ValueError:
            pass
        china = ZoneInfo("Asia/Shanghai")
        local_observed = observed_at.astimezone(china)
        clock = re.fullmatch(
            r"(?:(今天|昨天)\s*)?(\d{1,2}):(\d{2})(?::(\d{2}))?", candidate
        )
        if clock:
            day = local_observed.date()
            if clock.group(1) == "昨天":
                day -= timedelta(days=1)
            parsed = datetime(
                day.year,
                day.month,
                day.day,
                int(clock.group(2)),
                int(clock.group(3)),
                int(clock.group(4) or 0),
                tzinfo=china,
            )
            return parsed.astimezone(timezone.utc)
        full_date = re.fullmatch(
            r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})(?::(\d{2}))?",
            candidate,
        )
        if full_date:
            parsed = datetime(
                int(full_date.group(1)),
                int(full_date.group(2)),
                int(full_date.group(3)),
                int(full_date.group(4)),
                int(full_date.group(5)),
                int(full_date.group(6) or 0),
                tzinfo=china,
            )
            return parsed.astimezone(timezone.utc)
        short_date = re.fullmatch(
            r"(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})(?::(\d{2}))?",
            candidate,
        )
        if short_date:
            parsed = datetime(
                local_observed.year,
                int(short_date.group(1)),
                int(short_date.group(2)),
                int(short_date.group(3)),
                int(short_date.group(4)),
                int(short_date.group(5) or 0),
                tzinfo=china,
            )
            if parsed > local_observed + timedelta(days=1):
                parsed = parsed.replace(year=parsed.year - 1)
            return parsed.astimezone(timezone.utc)
        return observed_at
    return observed_at


def build_fingerprint(
    *,
    conversation_id: str,
    direction: str,
    kind: str,
    content: str | None,
    occurred_at: datetime,
    platform_message_id: str | None,
    dom_key: str | None = None,
    asset_identity: str | None = None,
) -> str:
    """优先使用平台 ID；缺失时使用时间桶和结构字段生成 SHA-256。"""
    if platform_message_id:
        material = f"platform:{conversation_id}:{platform_message_id}"
    else:
        bucket = int(occurred_at.timestamp() // 5)
        material = "|".join(
            [
                conversation_id,
                direction,
                kind,
                content or "",
                asset_identity or "",
                str(bucket),
            ]
        )
    return sha256(material.encode("utf-8")).hexdigest()


def parse_browser_message(
    raw: dict[str, Any], *, observed_at: datetime | None = None, is_backfill: bool = False
) -> BrowserMessage:
    """拒绝缺少会话标识的数据，并限制页面字段进入业务层的形状。"""
    observed_at = observed_at or datetime.now(timezone.utc)
    conversation_id = normalize_platform_customer_id(raw.get("conversation_id"))
    if not conversation_id:
        raise ValueError("页面消息缺少 conversation_id")
    platform_customer_id = normalize_platform_customer_id(
        raw.get("platform_customer_id") or conversation_id
    )
    if platform_customer_id != conversation_id:
        raise ValueError("页面消息归属与 conversation_id 不一致")
    direction = str(raw.get("direction") or "inbound").strip().lower()
    if direction not in {"inbound", "outbound"}:
        direction = "inbound"
    kind = str(raw.get("kind") or "unsupported").strip().lower()
    if kind not in {"text", "image", "emoji", "goods_card", "unsupported"}:
        kind = "unsupported"
    content = str(raw["content"]).strip() if raw.get("content") is not None else None
    platform_message_id = (
        str(raw["message_id"]).strip() if raw.get("message_id") else None
    )
    occurred_at = _parse_datetime(raw.get("timestamp"), observed_at)
    sender_type = str(
        raw.get("sender_type") or ("customer" if direction == "inbound" else "agent")
    ).strip().lower()
    if sender_type not in {"customer", "agent", "automation", "system"}:
        sender_type = "customer" if direction == "inbound" else "agent"
    assets: list[BrowserAsset] = []
    for candidate in raw.get("assets") or []:
        if not isinstance(candidate, dict):
            continue
        asset_type = str(candidate.get("asset_type") or "").strip().lower()
        if asset_type not in {"image", "emoji", "goods_image", "screenshot"}:
            continue
        digest = str(candidate.get("sha256") or "").strip().lower() or None
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
            digest = None
        metadata = candidate.get("metadata")
        width = candidate.get("width")
        height = candidate.get("height")
        assets.append(
            BrowserAsset(
                asset_type=asset_type,
                source_url=str(candidate.get("source_url") or "").strip()[:4000] or None,
                storage_key=str(candidate.get("storage_key") or "").strip()[:500] or None,
                mime_type=str(candidate.get("mime_type") or "").strip()[:100] or None,
                sha256=digest,
                width=int(width) if isinstance(width, (int, float)) and 0 < width <= 20000 else None,
                height=int(height) if isinstance(height, (int, float)) and 0 < height <= 20000 else None,
                metadata=dict(metadata) if isinstance(metadata, dict) else {},
            )
        )
    asset_identity = "|".join(
        filter(
            None,
            [
                str(raw.get("goods_id") or ""),
                *(asset.sha256 or asset.source_url or "" for asset in assets),
            ],
        )
    )
    fingerprint = build_fingerprint(
        conversation_id=conversation_id,
        direction=direction,
        kind=kind,
        content=content,
        occurred_at=occurred_at,
        platform_message_id=platform_message_id,
        dom_key=str(raw.get("dom_key") or ""),
        asset_identity=asset_identity,
    )
    return BrowserMessage(
        platform_conversation_id=conversation_id,
        display_name=str(raw.get("display_name") or "顾客").strip()[:255] or "顾客",
        avatar_url=str(raw.get("avatar_url") or "").strip()[:1000] or None,
        direction=direction,
        kind=kind,
        content=content,
        occurred_at=occurred_at,
        platform_message_id=platform_message_id,
        goods_id=str(raw.get("goods_id") or "").strip() or None,
        goods_name=str(raw.get("goods_name") or "").strip()[:500] or None,
        goods_price=str(raw.get("goods_price") or "").strip()[:64] or None,
        goods_url=str(raw.get("goods_url") or "").strip()[:2000] or None,
        is_backfill=is_backfill,
        fingerprint=fingerprint,
        platform_customer_id=platform_customer_id,
        sender_type=sender_type,
        sender_name=str(raw.get("sender_name") or "").strip()[:255] or None,
        assets=tuple(assets),
    )
