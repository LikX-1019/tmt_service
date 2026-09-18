"""客服控制台的 MySQL/SQLAlchemy 持久化实现。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, desc, func, or_, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import NO_VALUE
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.greeting import (
    default_reply_templates,
    default_trigger_groups,
    normalize_reply_templates,
)
from app.integrations.pdd.base import normalize_platform_customer_id
from app.models.conversation import (
    AgentAccount,
    ConnectorEvent,
    Conversation,
    Customer,
    CustomerNote,
    KnowledgeGap,
    Message,
    MessageAsset,
    OutboundJob,
    ReplyDecision,
    Shop,
    ShopGreetingConfig,
    ShopHandoffConfig,
    default_handoff_reply_template,
)
from app.models.knowledge import QAKnowledge


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    # MySQL may return a naive value even for DateTime(timezone=True). Stored
    # console timestamps are UTC, so preserve that contract in every API field.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _message_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Asia/Shanghai")).date()


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def conversation_to_dict(item: Conversation) -> dict[str, Any]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "platform_conversation_id": item.platform_conversation_id,
        "customer_id": item.customer_id,
        "display_name": item.display_name,
        "avatar_url": item.avatar_url,
        "goods_id": item.goods_id,
        "goods_name": item.goods_name,
        "goods_price": item.goods_price,
        "goods_url": item.goods_url,
        "state": item.state,
        "last_outbound_status": item.last_outbound_status,
        "auto_reply_enabled": item.auto_reply_enabled,
        "unread_count": item.unread_count,
        "last_message_at": _iso(item.last_message_at),
        "response_started_at": _iso(item.response_started_at),
        "response_deadline_at": _iso(item.response_deadline_at),
        "updated_at": _iso(item.updated_at),
    }


def message_to_dict(item: Message) -> dict[str, Any]:
    state = sa_inspect(item)
    loaded_assets = state.attrs.assets.loaded_value
    assets = [] if loaded_assets is NO_VALUE else loaded_assets
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "direction": item.direction,
        "kind": item.kind,
        "content": item.content,
        "occurred_at": _iso(item.occurred_at),
        "message_date": item.message_date.isoformat() if item.message_date else None,
        "sender_type": item.sender_type,
        "sender_account_id": item.sender_account_id,
        "sender_display_name": item.sender_display_name,
        "is_backfill": item.is_backfill,
        "outbound_job_id": item.outbound_job_id,
        "assets": [
            {
                "id": asset.id,
                "asset_type": asset.asset_type,
                # 原始拼多多地址可能包含临时签名或依赖登录 Cookie，不返回给中台前端。
                "source_url": None,
                "storage_key": asset.storage_key,
                "mime_type": asset.mime_type,
                "sha256": asset.sha256,
                "width": asset.width,
                "height": asset.height,
                "metadata": asset.metadata_json or {},
            }
            for asset in assets
        ],
    }


def job_to_dict(item: OutboundJob) -> dict[str, Any]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "conversation_id": item.conversation_id,
        "client_request_id": item.client_request_id,
        "source": item.source,
        "content": item.content,
        "status": item.status,
        "error_code": item.error_code,
        "responder_account_id": item.responder_account_id,
        "responder_display_name": item.responder_display_name,
        "clicked_at": _iso(item.clicked_at),
        "sent_at": _iso(item.sent_at),
        "response_deadline_at": _iso(item.response_deadline_at),
        "created_at": _iso(item.created_at),
    }


def decision_to_dict(item: ReplyDecision | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "route": item.route,
        "action": item.action,
        "greeting_type": item.greeting_type,
        "recognition_source": item.recognition_source,
        "qa_code": item.qa_code,
        "product_id": item.product_id,
        "product_name": item.product_name,
        "top_score": item.top_score,
        "score_margin": item.score_margin,
        "risk_reason": item.risk_reason,
        "suggested_answer": item.suggested_answer,
        "policy_version": item.policy_version,
        "created_at": _iso(item.created_at),
    }


def greeting_config_to_dict(item: ShopGreetingConfig) -> dict[str, Any]:
    return {
        "enabled": item.enabled,
        "trigger_groups": item.trigger_groups,
        "reply_templates": normalize_reply_templates(item.reply_templates),
        "updated_at": _iso(item.updated_at),
    }


def handoff_config_to_dict(item: ShopHandoffConfig) -> dict[str, Any]:
    return {
        "reply_template": item.reply_template,
        "updated_at": _iso(item.updated_at),
    }


def shop_to_dict(item: Shop) -> dict[str, Any]:
    return {
        "id": item.id,
        "platform": item.platform,
        "name": item.name,
        "platform_shop_id": item.platform_shop_id,
        "lifecycle_status": item.lifecycle_status,
        "desired_online": item.desired_online,
        "browser_profile_key": item.browser_profile_key,
        "reception_mode": item.reception_mode,
        "identified_at": _iso(item.identified_at),
        "last_error_code": item.last_error_code,
        "enabled": item.enabled,
        "global_auto_reply_enabled": item.global_auto_reply_enabled,
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }


def note_to_dict(item: CustomerNote | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "customer_id": item.customer_id,
        "shop_id": item.shop_id,
        "content": item.content,
        "tags": list(item.tags or []),
        "pinned": item.pinned,
        "updated_at": _iso(item.updated_at),
    }


def knowledge_gap_to_dict(item: KnowledgeGap) -> dict[str, Any]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "product_id": item.product_id or None,
        "normalized_question": item.normalized_question,
        "example_question": item.example_question,
        "reason_code": item.reason_code,
        "occurrences": item.occurrences,
        "status": item.status,
        "candidate_answer": item.candidate_answer,
        "linked_qa_code": item.linked_qa_code,
        "first_seen_at": _iso(item.first_seen_at),
        "last_seen_at": _iso(item.last_seen_at),
    }


def _same_idempotent_request(
    item: OutboundJob, conversation_id: str, source: str, content: str
) -> bool:
    return (
        item.conversation_id == conversation_id
        and item.source == source
        and item.content == content
    )


class ConsoleRepository:
    """每个方法独立提交，保证浏览器崩溃时状态已经落库。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_shop(self, name: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = await session.scalar(
                select(Shop).where(Shop.platform == "pdd").order_by(Shop.created_at)
            )
            if item is None:
                item = Shop(
                    name=name,
                    platform="pdd",
                    lifecycle_status="active",
                    browser_profile_key="legacy",
                )
                session.add(item)
                await session.commit()
                await session.refresh(item)
            return shop_to_dict(item)

    async def create_provisioning_shop(
        self, *, name: str = "待识别店铺", browser_profile_key: str
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = Shop(
                name=name,
                platform="pdd",
                lifecycle_status="provisioning",
                desired_online=True,
                browser_profile_key=browser_profile_key,
                reception_mode="assist",
                enabled=True,
                global_auto_reply_enabled=False,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return shop_to_dict(item)

    async def list_shops(
        self, *, include_disabled: bool = True
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            statement = select(Shop).order_by(Shop.created_at, Shop.id)
            if not include_disabled:
                statement = statement.where(Shop.lifecycle_status != "disabled")
            items = list(await session.scalars(statement))
            result: list[dict[str, Any]] = []
            now = datetime.now(timezone.utc)
            warning_boundary = now + timedelta(seconds=30)
            for item in items:
                summary = shop_to_dict(item)
                counts = (
                    await session.execute(
                        select(
                            func.count(Conversation.id),
                            func.sum(case((Conversation.state == "pending", 1), else_=0)),
                            func.sum(
                                case(
                                    (
                                        Conversation.response_deadline_at.is_not(None)
                                        & (Conversation.response_deadline_at <= warning_boundary)
                                        & (Conversation.response_deadline_at > now),
                                        1,
                                    ),
                                    else_=0,
                                )
                            ),
                            func.sum(
                                case(
                                    (
                                        Conversation.response_deadline_at.is_not(None)
                                        & (Conversation.response_deadline_at <= now),
                                        1,
                                    ),
                                    else_=0,
                                )
                            ),
                        ).where(Conversation.shop_id == item.id)
                    )
                ).one()
                gap_count = await session.scalar(
                    select(func.count(KnowledgeGap.id)).where(
                        KnowledgeGap.shop_id == item.id,
                        KnowledgeGap.status == "open",
                    )
                )
                summary["counts"] = {
                    "conversations": int(counts[0] or 0),
                    "pending": int(counts[1] or 0),
                    "due_soon": int(counts[2] or 0),
                    "overdue": int(counts[3] or 0),
                    "knowledge_gaps": int(gap_count or 0),
                }
                result.append(summary)
            return result

    async def get_shop(self, shop_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.get(Shop, shop_id)
            if item is None:
                return None
            return shop_to_dict(item)

    async def identify_shop(
        self, shop_id: str, *, platform_shop_id: str, name: str
    ) -> tuple[dict[str, Any], str | None]:
        """绑定稳定平台账号；重复时返回已有店铺 ID，绝不按名称猜测。"""
        platform_shop_id = platform_shop_id.strip()
        name = name.strip()
        if not platform_shop_id or not name:
            raise ValueError("平台店铺 ID 和名称不能为空")
        async with self._session_factory() as session:
            item = await session.get(Shop, shop_id)
            if item is None:
                raise LookupError("店铺不存在")
            duplicate = await session.scalar(
                select(Shop).where(
                    Shop.platform == item.platform,
                    Shop.platform_shop_id == platform_shop_id,
                    Shop.id != shop_id,
                )
            )
            if duplicate is not None:
                item.lifecycle_status = "failed"
                item.desired_online = False
                item.last_error_code = "DUPLICATE_SHOP_ACCOUNT"
                await session.commit()
                return shop_to_dict(item), duplicate.id
            item.platform_shop_id = platform_shop_id
            item.name = name[:255]
            item.lifecycle_status = "active"
            item.identified_at = datetime.now(timezone.utc)
            item.last_error_code = None
            await session.commit()
            await session.refresh(item)
            return shop_to_dict(item), None

    async def update_shop_state(
        self,
        shop_id: str,
        *,
        lifecycle_status: str | None = None,
        desired_online: bool | None = None,
        enabled: bool | None = None,
        reception_mode: str | None = None,
        last_error_code: str | None = None,
    ) -> dict[str, Any]:
        if lifecycle_status is not None and lifecycle_status not in {
            "provisioning",
            "active",
            "failed",
            "disabled",
        }:
            raise ValueError("无效店铺生命周期状态")
        if reception_mode is not None and reception_mode not in {
            "assist",
            "guarded_auto",
        }:
            raise ValueError("无效接待模式")
        async with self._session_factory() as session:
            item = await session.get(Shop, shop_id)
            if item is None:
                raise LookupError("店铺不存在")
            if lifecycle_status is not None:
                item.lifecycle_status = lifecycle_status
            if desired_online is not None:
                item.desired_online = desired_online
            if enabled is not None:
                item.enabled = enabled
            if reception_mode is not None:
                item.reception_mode = reception_mode
            item.last_error_code = last_error_code
            await session.commit()
            await session.refresh(item)
            return shop_to_dict(item)

    async def set_global_automation(self, shop_id: str, enabled: bool) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = await session.get(Shop, shop_id)
            if item is None:
                raise LookupError("店铺不存在")
            item.global_auto_reply_enabled = enabled
            await session.commit()
            return {"enabled": enabled}

    async def get_greeting_config(self, shop_id: str) -> dict[str, Any]:
        """读取店铺配置；历史店铺没有配置时懒创建默认值。"""
        async with self._session_factory() as session:
            item = await session.get(ShopGreetingConfig, shop_id)
            if item is None:
                if await session.get(Shop, shop_id) is None:
                    raise LookupError("店铺不存在")
                item = ShopGreetingConfig(
                    shop_id=shop_id,
                    enabled=True,
                    trigger_groups=default_trigger_groups(),
                    reply_templates=default_reply_templates(),
                )
                session.add(item)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    item = await session.get(ShopGreetingConfig, shop_id)
                    if item is None:
                        raise
                else:
                    await session.refresh(item)
            return greeting_config_to_dict(item)

    async def update_greeting_config(
        self,
        shop_id: str,
        *,
        enabled: bool,
        trigger_groups: dict[str, list[str]],
        reply_templates: dict[str, list[str]],
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            if await session.get(Shop, shop_id) is None:
                raise LookupError("店铺不存在")
            item = await session.get(ShopGreetingConfig, shop_id)
            if item is None:
                item = ShopGreetingConfig(shop_id=shop_id)
                session.add(item)
            item.enabled = enabled
            item.trigger_groups = dict(trigger_groups)
            item.reply_templates = normalize_reply_templates(reply_templates)
            item.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(item)
            return greeting_config_to_dict(item)

    async def get_handoff_config(self, shop_id: str) -> dict[str, Any]:
        """读取售后接管话术；历史店铺首次使用时创建安全默认值。"""
        async with self._session_factory() as session:
            item = await session.get(ShopHandoffConfig, shop_id)
            if item is None:
                if await session.get(Shop, shop_id) is None:
                    raise LookupError("店铺不存在")
                item = ShopHandoffConfig(
                    shop_id=shop_id, reply_template=default_handoff_reply_template()
                )
                session.add(item)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    item = await session.get(ShopHandoffConfig, shop_id)
                    if item is None:
                        raise
                else:
                    await session.refresh(item)
            return handoff_config_to_dict(item)

    async def update_handoff_config(
        self, shop_id: str, *, reply_template: str
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            if await session.get(Shop, shop_id) is None:
                raise LookupError("店铺不存在")
            item = await session.get(ShopHandoffConfig, shop_id)
            if item is None:
                item = ShopHandoffConfig(shop_id=shop_id)
                session.add(item)
            item.reply_template = reply_template
            item.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(item)
            return handoff_config_to_dict(item)

    async def _resolve_profile(
        self,
        session: AsyncSession,
        shop_id: str,
        *,
        platform_customer_id: str,
        display_name: str,
        avatar_url: str | None,
    ) -> tuple[Customer, Conversation, bool]:
        """按平台顾客 ID 归一身份，并把旧的名称/头像会话安全迁移到稳定 ID。"""
        platform_customer_id = normalize_platform_customer_id(platform_customer_id)
        if not platform_customer_id:
            raise ValueError("平台顾客 ID 不能为空")
        customer = await session.scalar(
            select(Customer).where(
                Customer.shop_id == shop_id,
                Customer.platform_customer_id == platform_customer_id,
            )
        )
        if customer is None and avatar_url:
            customer = await session.scalar(
                select(Customer).where(
                    Customer.shop_id == shop_id,
                    Customer.avatar_url == avatar_url,
                    Customer.platform_customer_id.like("legacy:%"),
                )
            )
        if customer is None and display_name not in {"", "顾客", "客服"}:
            candidates = list(
                await session.scalars(
                    select(Customer).where(
                        Customer.shop_id == shop_id,
                        Customer.display_name == display_name,
                        Customer.platform_customer_id.like("legacy:%"),
                    )
                )
            )
            if len(candidates) == 1:
                customer = candidates[0]

        changed = False
        now = datetime.now(timezone.utc)
        if customer is None:
            customer = Customer(
                shop_id=shop_id,
                platform_customer_id=platform_customer_id,
                display_name=display_name,
                avatar_url=avatar_url,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(customer)
            await session.flush()
            changed = True
        else:
            if customer.platform_customer_id != platform_customer_id:
                customer.platform_customer_id = platform_customer_id
                changed = True
            if display_name not in {"", "顾客", "客服"} and customer.display_name != display_name:
                customer.display_name = display_name
                changed = True
            if avatar_url and customer.avatar_url != avatar_url:
                customer.avatar_url = avatar_url
                changed = True
            customer.last_seen_at = now

        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.shop_id == shop_id,
                Conversation.customer_id == customer.id,
            )
        )
        if conversation is None:
            conversation = Conversation(
                shop_id=shop_id,
                customer_id=customer.id,
                platform_conversation_id=platform_customer_id,
                display_name=customer.display_name,
                avatar_url=customer.avatar_url,
            )
            session.add(conversation)
            await session.flush()
            changed = True
        else:
            if conversation.platform_conversation_id != platform_customer_id:
                conversation.platform_conversation_id = platform_customer_id
                changed = True
            if conversation.display_name != customer.display_name:
                conversation.display_name = customer.display_name
                changed = True
            if customer.avatar_url and conversation.avatar_url != customer.avatar_url:
                conversation.avatar_url = customer.avatar_url
                changed = True
        return customer, conversation, changed

    async def upsert_customer_profile(
        self, shop_id: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session:
            async with session.begin():
                _, conversation, changed = await self._resolve_profile(
                    session,
                    shop_id,
                    platform_customer_id=str(payload["platform_customer_id"]),
                    display_name=str(payload.get("display_name") or "顾客")[:255],
                    avatar_url=str(payload.get("avatar_url") or "")[:1000] or None,
                )
                await session.flush()
                return conversation_to_dict(conversation), changed

    async def _resolve_agent_account(
        self,
        session: AsyncSession,
        shop_id: str,
        *,
        display_name: str | None,
        sender_type: str,
    ) -> AgentAccount | None:
        if not display_name or sender_type not in {"agent", "automation"}:
            return None
        account = await session.scalar(
            select(AgentAccount).where(
                AgentAccount.shop_id == shop_id,
                AgentAccount.display_name == display_name,
                AgentAccount.account_type == sender_type,
            )
        )
        now = datetime.now(timezone.utc)
        if account is None:
            account = AgentAccount(
                shop_id=shop_id,
                display_name=display_name,
                account_type=sender_type,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(account)
            await session.flush()
        else:
            account.last_seen_at = now
        return account

    async def ingest_message(
        self,
        shop_id: str,
        payload: dict[str, Any],
        *,
        response_timeout_seconds: int = 160,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        async with self._session_factory() as session:
            async with session.begin():
                platform_customer_id = str(
                    payload.get("platform_customer_id")
                    or payload["platform_conversation_id"]
                )
                customer, conversation, _ = await self._resolve_profile(
                    session,
                    shop_id,
                    platform_customer_id=platform_customer_id,
                    display_name=str(payload.get("display_name") or "顾客")[:255],
                    avatar_url=str(payload.get("avatar_url") or "")[:1000] or None,
                )
                if payload.get("goods_id"):
                    conversation.goods_id = payload["goods_id"]
                if payload.get("goods_name"):
                    conversation.goods_name = payload["goods_name"]
                if payload.get("goods_price"):
                    conversation.goods_price = str(payload["goods_price"])[:64]
                if payload.get("goods_url"):
                    conversation.goods_url = str(payload["goods_url"])[:2000]
                sender_type = str(
                    payload.get("sender_type")
                    or ("customer" if payload["direction"] == "inbound" else "agent")
                )
                sender_name = str(payload.get("sender_name") or "").strip()[:255] or None
                account = await self._resolve_agent_account(
                    session,
                    shop_id,
                    display_name=sender_name,
                    sender_type=sender_type,
                )
                existing = await session.scalar(
                    select(Message)
                    .options(selectinload(Message.assets))
                    .where(
                        Message.shop_id == shop_id,
                        Message.fingerprint == payload["fingerprint"],
                    )
                )
                if existing is None and payload.get("platform_message_id"):
                    same_platform = list(
                        await session.scalars(
                            select(Message).options(selectinload(Message.assets)).where(
                                Message.shop_id == shop_id,
                                Message.platform_message_id
                                == payload["platform_message_id"]
                            )
                        )
                    )
                    verified_elsewhere = next(
                        (
                            item
                            for item in same_platform
                            if item.assignment_verified
                            and item.conversation_id != conversation.id
                        ),
                        None,
                    )
                    if verified_elsewhere is not None:
                        raise ValueError("同一平台消息已归属于其他顾客")
                    existing = next(
                        (
                            item
                            for item in same_platform
                            if item.conversation_id == conversation.id
                        ),
                        None,
                    )
                if existing is not None:
                    existing.fingerprint = payload["fingerprint"]
                    existing.shop_id = shop_id
                    existing.customer_id = customer.id
                    existing.message_date = _message_date(payload["occurred_at"])
                    existing.sender_type = sender_type
                    existing.sender_account_id = account.id if account else None
                    existing.sender_display_name = sender_name
                    existing.assignment_verified = True
                    if not getattr(existing, "assets", None):
                        for asset in payload.get("assets") or []:
                            if isinstance(asset, dict):
                                session.add(MessageAsset(
                                    message_id=existing.id,
                                    asset_type=str(asset.get("asset_type") or "unsupported")[:32],
                                    source_url=str(asset.get("source_url") or "")[:4000] or None,
                                    storage_key=str(asset.get("storage_key") or "")[:500] or None,
                                    mime_type=str(asset.get("mime_type") or "")[:100] or None,
                                    sha256=str(asset.get("sha256") or "")[:64] or None,
                                    width=asset.get("width"), height=asset.get("height"),
                                    metadata_json=asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {},
                                ))
                    await session.flush()
                    return conversation_to_dict(conversation), message_to_dict(existing), False
                message = Message(
                    conversation_id=conversation.id,
                    shop_id=shop_id,
                    customer_id=customer.id,
                    platform_message_id=payload.get("platform_message_id"),
                    fingerprint=payload["fingerprint"],
                    direction=payload["direction"],
                    kind=payload["kind"],
                    content=payload.get("content"),
                    occurred_at=payload["occurred_at"],
                    message_date=_message_date(payload["occurred_at"]),
                    sender_type=sender_type,
                    sender_account_id=account.id if account else None,
                    sender_display_name=sender_name,
                    assignment_verified=True,
                    is_backfill=bool(payload.get("is_backfill")),
                )
                session.add(message)
                for asset in payload.get("assets") or []:
                    if isinstance(asset, dict):
                        message.assets.append(MessageAsset(
                            asset_type=str(asset.get("asset_type") or "unsupported")[:32],
                            source_url=str(asset.get("source_url") or "")[:4000] or None,
                            storage_key=str(asset.get("storage_key") or "")[:500] or None,
                            mime_type=str(asset.get("mime_type") or "")[:100] or None,
                            sha256=str(asset.get("sha256") or "")[:64] or None,
                            width=asset.get("width"), height=asset.get("height"),
                            metadata_json=asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {},
                        ))
                conversation.last_message_at = payload["occurred_at"]
                is_live = not bool(payload.get("is_backfill"))
                if is_live:
                    if payload["direction"] == "inbound":
                        received_at = datetime.now(timezone.utc)
                        conversation.response_started_at = received_at
                        conversation.response_deadline_at = received_at + timedelta(
                            seconds=response_timeout_seconds
                        )
                        conversation.unread_count += 1
                        if conversation.state != "manual":
                            conversation.state = "pending"
                    elif payload["direction"] == "outbound":
                        conversation.response_started_at = None
                        conversation.response_deadline_at = None
                await session.flush()
                return conversation_to_dict(conversation), message_to_dict(message), True

    async def list_conversations(
        self,
        shop_id: str,
        *,
        limit: int,
        state: str | None = None,
        search: str | None = None,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(Conversation).where(Conversation.shop_id == shop_id)
        if state:
            statement = statement.where(Conversation.state == state)
        if search:
            pattern = f"%{search}%"
            statement = statement.where(
                or_(
                    Conversation.display_name.like(pattern),
                    Conversation.goods_name.like(pattern),
                )
            )
        if before:
            statement = statement.where(Conversation.last_message_at < before)
        statement = statement.order_by(
            case((Conversation.response_deadline_at.is_(None), 1), else_=0),
            Conversation.response_deadline_at,
            desc(Conversation.last_message_at),
            desc(Conversation.id),
        ).limit(limit)
        async with self._session_factory() as session:
            items = (await session.scalars(statement)).all()
            return [conversation_to_dict(item) for item in items]

    async def get_conversation(
        self, conversation_id: str, shop_id: str | None = None
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.get(Conversation, conversation_id)
            if item is not None and shop_id is not None and item.shop_id != shop_id:
                return None
            return conversation_to_dict(item) if item else None

    async def mark_conversation_read(
        self, conversation_id: str, shop_id: str | None = None
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = await session.get(Conversation, conversation_id)
            if item is None or (shop_id is not None and item.shop_id != shop_id):
                raise LookupError("会话不存在")
            item.unread_count = 0
            await session.commit()
            return conversation_to_dict(item)

    async def clear_conversation_response_timer(
        self, conversation_id: str, shop_id: str | None = None
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = await session.get(Conversation, conversation_id)
            if item is None or (shop_id is not None and item.shop_id != shop_id):
                raise LookupError("会话不存在")
            item.response_started_at = None
            item.response_deadline_at = None
            await session.commit()
            return conversation_to_dict(item)

    async def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int,
        before: datetime | None = None,
        shop_id: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(Message).options(selectinload(Message.assets)).where(
            Message.conversation_id == conversation_id,
            Message.assignment_verified.is_(True),
        )
        if shop_id is not None:
            statement = statement.where(Message.shop_id == shop_id)
        if before:
            statement = statement.where(Message.occurred_at < before)
        statement = statement.order_by(desc(Message.occurred_at), desc(Message.id)).limit(limit)
        async with self._session_factory() as session:
            items = list((await session.scalars(statement)).all())
            items.reverse()
            return [message_to_dict(item) for item in items]

    async def list_customers(
        self, shop_id: str, *, limit: int = 100, search: str | None = None
    ) -> list[dict[str, Any]]:
        statement = select(Customer).where(Customer.shop_id == shop_id)
        if search:
            statement = statement.where(Customer.display_name.like(f"%{search}%"))
        statement = statement.order_by(desc(Customer.last_seen_at)).limit(limit)
        async with self._session_factory() as session:
            items = list(await session.scalars(statement))
            return [
                {
                    "id": item.id,
                    "platform_customer_id": item.platform_customer_id,
                    "display_name": item.display_name,
                    "avatar_url": item.avatar_url,
                    "first_seen_at": _iso(item.first_seen_at),
                    "last_seen_at": _iso(item.last_seen_at),
                }
                for item in items
            ]

    async def list_customer_messages(
        self,
        customer_id: str,
        *,
        message_date: date | None = None,
        limit: int = 200,
        shop_id: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(Message).options(selectinload(Message.assets)).where(
            Message.customer_id == customer_id,
            Message.assignment_verified.is_(True),
        )
        if shop_id is not None:
            statement = statement.where(Message.shop_id == shop_id)
        if message_date:
            statement = statement.where(Message.message_date == message_date)
        statement = statement.order_by(desc(Message.occurred_at), desc(Message.id)).limit(limit)
        async with self._session_factory() as session:
            items = list(await session.scalars(statement))
            items.reverse()
            return [message_to_dict(item) for item in items]

    async def set_conversation_automation(
        self, conversation_id: str, enabled: bool, shop_id: str | None = None
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = await session.get(Conversation, conversation_id)
            if item is None or (shop_id is not None and item.shop_id != shop_id):
                raise LookupError("会话不存在")
            item.auto_reply_enabled = enabled
            item.state = "pending" if enabled else "manual"
            if not enabled:
                item.unread_count = 0
            await session.commit()
            return conversation_to_dict(item)

    async def create_outbound_job(
        self,
        conversation_id: str,
        *,
        client_request_id: str,
        source: str,
        content: str,
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                raise LookupError("会话不存在")
            shop_id = conversation.shop_id
            existing = await session.scalar(
                select(OutboundJob).where(
                    OutboundJob.shop_id == shop_id,
                    OutboundJob.client_request_id == client_request_id
                )
            )
            if existing:
                if not _same_idempotent_request(
                    existing, conversation_id, source, content
                ):
                    raise ValueError("client_request_id 已用于另一发送请求")
                return job_to_dict(existing), False
            job = OutboundJob(
                shop_id=shop_id,
                conversation_id=conversation_id,
                client_request_id=client_request_id,
                source=source,
                content=content,
                response_deadline_at=conversation.response_deadline_at,
            )
            session.add(job)
            if source == "manual":
                conversation.auto_reply_enabled = False
                conversation.state = "manual"
                conversation.unread_count = 0
            conversation.last_outbound_status = "queued"
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(OutboundJob).where(
                        OutboundJob.shop_id == shop_id,
                        OutboundJob.client_request_id == client_request_id
                    )
                )
                if existing is None:
                    raise
                if not _same_idempotent_request(
                    existing, conversation_id, source, content
                ):
                    raise ValueError("client_request_id 已用于另一发送请求")
                return job_to_dict(existing), False
            await session.refresh(job)
            return job_to_dict(job), True

    async def handoff_conversation(
        self,
        conversation_id: str,
        *,
        client_request_id: str,
        content: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        """原子关闭自动接待并可选地入队固定转人工话术。"""
        async with self._session_factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                raise LookupError("会话不存在")
            shop_id = conversation.shop_id
            conversation.auto_reply_enabled = False
            conversation.state = "manual"
            job: OutboundJob | None = None
            created = False
            if content:
                existing = await session.scalar(
                    select(OutboundJob).where(
                        OutboundJob.shop_id == shop_id,
                        OutboundJob.client_request_id == client_request_id
                    )
                )
                if existing is not None:
                    if not _same_idempotent_request(
                        existing, conversation_id, "handoff", content
                    ):
                        raise ValueError("client_request_id 已用于另一发送请求")
                    job = existing
                else:
                    job = OutboundJob(
                        shop_id=shop_id,
                        conversation_id=conversation_id,
                        client_request_id=client_request_id,
                        source="handoff",
                        content=content,
                        response_deadline_at=conversation.response_deadline_at,
                    )
                    session.add(job)
                    conversation.last_outbound_status = "queued"
                    created = True
            await session.commit()
            if job is not None:
                await session.refresh(job)
            return conversation_to_dict(conversation), job_to_dict(job) if job else None, created

    async def get_job(
        self, job_id: str, shop_id: str | None = None
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.get(OutboundJob, job_id)
            if item is not None and shop_id is not None and item.shop_id != shop_id:
                return None
            return job_to_dict(item) if item else None

    async def recover_queued_jobs(
        self, shop_id: str | None = None
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            # 进程可能在点击前后任一时刻退出，sending 无法证明未点击，只能转 uncertain。
            sending_filter = [OutboundJob.status == "sending"]
            queued_filter = [OutboundJob.status == "queued"]
            if shop_id is not None:
                sending_filter.append(OutboundJob.shop_id == shop_id)
                queued_filter.append(OutboundJob.shop_id == shop_id)
            sending_conversations = list(
                await session.scalars(
                    select(OutboundJob.conversation_id).where(
                        *sending_filter
                    )
                )
            )
            await session.execute(
                update(OutboundJob)
                .where(*sending_filter)
                .values(status="uncertain", error_code="RECOVERY_UNCERTAIN")
            )
            if sending_conversations:
                await session.execute(
                    update(Conversation)
                    .where(Conversation.id.in_(sending_conversations))
                    .values(last_outbound_status="uncertain")
                )
            await session.commit()
            items = (
                await session.scalars(
                    select(OutboundJob)
                    .where(*queued_filter)
                    .order_by(OutboundJob.created_at)
                )
            ).all()
            return [job_to_dict(item) for item in items]

    async def cancel_queued_jobs(self, shop_id: str) -> int:
        """下线时仅取消尚未开始点击的任务。"""
        async with self._session_factory() as session:
            result = await session.execute(
                update(OutboundJob)
                .where(
                    OutboundJob.shop_id == shop_id,
                    OutboundJob.status == "queued",
                )
                .values(status="cancelled", error_code="SHOP_OFFLINE")
            )
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.shop_id == shop_id,
                    Conversation.last_outbound_status == "queued",
                )
                .values(last_outbound_status="cancelled")
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def update_job(
        self,
        job_id: str,
        *,
        status: str,
        error_code: str | None = None,
        clicked_at: datetime | None = None,
        sent_at: datetime | None = None,
        responder_name: str | None = None,
        shop_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            job = await session.get(OutboundJob, job_id)
            if job is None or (shop_id is not None and job.shop_id != shop_id):
                raise LookupError("发送任务不存在")
            allowed = {
                "queued": {"sending", "failed", "cancelled"},
                "sending": {"sent", "uncertain", "failed"},
            }
            if status != job.status and status not in allowed.get(job.status, set()):
                raise ValueError(f"非法发送状态迁移：{job.status} -> {status}")
            job.status = status
            job.error_code = error_code
            job.clicked_at = clicked_at or job.clicked_at
            job.sent_at = sent_at or job.sent_at
            conversation = await session.get(Conversation, job.conversation_id)
            if responder_name and conversation:
                account = await self._resolve_agent_account(
                    session,
                    conversation.shop_id,
                    display_name=responder_name,
                    sender_type="automation" if job.source in {"auto", "handoff"} else "agent",
                )
                job.responder_account_id = account.id if account else None
                job.responder_display_name = responder_name
            if conversation:
                conversation.last_outbound_status = status
            if status == "sent":
                if conversation:
                    conversation.state = "auto_replied" if job.source == "auto" else "manual"
                    conversation.unread_count = 0
                    conversation.last_message_at = sent_at or datetime.now(timezone.utc)
                    conversation.response_started_at = None
                    conversation.response_deadline_at = None
            await session.commit()
            await session.refresh(job)
            return job_to_dict(job)

    async def add_decision(
        self, conversation_id: str, data: dict[str, Any], shop_id: str | None = None
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or (
                shop_id is not None and conversation.shop_id != shop_id
            ):
                raise LookupError("会话不存在")
            shop_id = conversation.shop_id
            existing = await session.scalar(
                select(ReplyDecision).where(
                    ReplyDecision.shop_id == shop_id,
                    ReplyDecision.batch_key == data["batch_key"],
                )
            )
            if existing:
                return decision_to_dict(existing) or {}
            item = ReplyDecision(
                shop_id=shop_id, conversation_id=conversation_id, **data
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return decision_to_dict(item) or {}

    async def latest_decision(
        self, conversation_id: str, shop_id: str | None = None
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            filters = [ReplyDecision.conversation_id == conversation_id]
            if shop_id is not None:
                filters.append(ReplyDecision.shop_id == shop_id)
            item = await session.scalar(
                select(ReplyDecision)
                .where(*filters)
                .order_by(desc(ReplyDecision.created_at))
                .limit(1)
            )
            return decision_to_dict(item)

    async def add_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        shop_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            if shop_id is not None:
                payload = {**payload, "shop_id": shop_id}
            item = ConnectorEvent(
                shop_id=shop_id, event_type=event_type, payload=payload
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return {
                "id": item.id,
                "event_type": item.event_type,
                "payload": item.payload,
                "created_at": _iso(item.created_at),
            }

    async def events_after(
        self, event_id: int, limit: int = 200, shop_id: str | None = None
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            filters = [ConnectorEvent.id > event_id]
            if shop_id is not None:
                filters.append(ConnectorEvent.shop_id == shop_id)
            items: Sequence[ConnectorEvent] = (
                await session.scalars(
                    select(ConnectorEvent)
                    .where(*filters)
                    .order_by(ConnectorEvent.id)
                    .limit(limit)
                )
            ).all()
            return [
                {
                    "id": item.id,
                    "event_type": item.event_type,
                    "payload": item.payload,
                    "created_at": _iso(item.created_at),
                }
                for item in items
            ]

    async def latest_event_id(self, shop_id: str | None = None) -> int:
        async with self._session_factory() as session:
            statement = select(func.max(ConnectorEvent.id))
            if shop_id is not None:
                statement = statement.where(ConnectorEvent.shop_id == shop_id)
            value = await session.scalar(statement)
            return int(value or 0)

    async def get_message_asset(
        self, asset_id: str, shop_id: str | None = None
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.get(MessageAsset, asset_id)
            if item is None or not item.storage_key:
                return None
            if shop_id is not None:
                message = await session.get(Message, item.message_id)
                if message is None or message.shop_id != shop_id:
                    return None
            return {
                "id": item.id,
                "storage_key": item.storage_key,
                "mime_type": item.mime_type or "application/octet-stream",
                "sha256": item.sha256,
            }

    async def get_customer_note(
        self, shop_id: str, customer_id: str
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            customer = await session.get(Customer, customer_id)
            if customer is None or customer.shop_id != shop_id:
                raise LookupError("顾客不存在")
            return note_to_dict(await session.get(CustomerNote, customer_id))

    async def update_customer_note(
        self,
        shop_id: str,
        customer_id: str,
        *,
        content: str,
        tags: list[str],
        pinned: bool,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            customer = await session.get(Customer, customer_id)
            if customer is None or customer.shop_id != shop_id:
                raise LookupError("顾客不存在")
            item = await session.get(CustomerNote, customer_id)
            if item is None:
                item = CustomerNote(customer_id=customer_id, shop_id=shop_id)
                session.add(item)
            item.content = content[:4000]
            item.tags = list(dict.fromkeys(tag.strip()[:40] for tag in tags if tag.strip()))[:20]
            item.pinned = pinned
            item.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(item)
            return note_to_dict(item) or {}

    async def record_knowledge_gap(
        self,
        shop_id: str,
        *,
        product_id: str | None,
        normalized_question: str,
        example_question: str,
        reason_code: str,
    ) -> dict[str, Any]:
        normalized_question = normalized_question.strip().lower()[:500]
        if not normalized_question:
            raise ValueError("规范化问题不能为空")
        product_key = (product_id or "").strip()[:64]
        async with self._session_factory() as session:
            item = await session.scalar(
                select(KnowledgeGap).where(
                    KnowledgeGap.shop_id == shop_id,
                    KnowledgeGap.product_id == product_key,
                    KnowledgeGap.normalized_question == normalized_question,
                    KnowledgeGap.reason_code == reason_code,
                )
            )
            now = datetime.now(timezone.utc)
            if item is None:
                item = KnowledgeGap(
                    shop_id=shop_id,
                    product_id=product_key,
                    normalized_question=normalized_question,
                    example_question=example_question[:4000],
                    reason_code=reason_code[:64],
                    occurrences=1,
                    status="open",
                    first_seen_at=now,
                    last_seen_at=now,
                )
                session.add(item)
            else:
                item.occurrences += 1
                item.example_question = example_question[:4000]
                item.last_seen_at = now
                if item.status == "resolved":
                    item.status = "open"
            await session.commit()
            await session.refresh(item)
            return knowledge_gap_to_dict(item)

    async def list_knowledge_gaps(
        self, shop_id: str, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        statement = select(KnowledgeGap).where(KnowledgeGap.shop_id == shop_id)
        if status:
            statement = statement.where(KnowledgeGap.status == status)
        statement = statement.order_by(
            desc(KnowledgeGap.occurrences), desc(KnowledgeGap.last_seen_at)
        ).limit(limit)
        async with self._session_factory() as session:
            return [
                knowledge_gap_to_dict(item)
                for item in (await session.scalars(statement)).all()
            ]

    async def update_knowledge_gap(
        self,
        shop_id: str,
        gap_id: str,
        *,
        status: str,
        candidate_answer: str | None = None,
        linked_qa_code: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"open", "draft", "resolved", "dismissed"}:
            raise ValueError("无效知识缺口状态")
        async with self._session_factory() as session:
            item = await session.get(KnowledgeGap, gap_id)
            if item is None or item.shop_id != shop_id:
                raise LookupError("知识缺口不存在")
            item.status = status
            item.candidate_answer = candidate_answer
            item.linked_qa_code = linked_qa_code
            await session.commit()
            await session.refresh(item)
            return knowledge_gap_to_dict(item)

    async def create_knowledge_gap_qa_draft(
        self,
        shop_id: str,
        gap_id: str,
        *,
        standard_answer: str,
    ) -> dict[str, Any]:
        """将缺口转换为默认不可检索、不可自动发送的 QA 草稿。"""
        standard_answer = standard_answer.strip()
        if not standard_answer:
            raise ValueError("标准答案不能为空")
        async with self._session_factory() as session:
            gap = await session.get(KnowledgeGap, gap_id)
            if gap is None or gap.shop_id != shop_id:
                raise LookupError("知识缺口不存在")
            if gap.status in {"resolved", "dismissed"}:
                raise ValueError("已关闭的知识缺口不能转换为草稿")

            qa_code = gap.linked_qa_code or f"KG-{gap.id}"
            existing = await session.scalar(
                select(QAKnowledge).where(QAKnowledge.qa_code == qa_code)
            )
            saved_answer = standard_answer[:4000]
            if existing is None:
                session.add(
                    QAKnowledge(
                        qa_code=qa_code,
                        product_code=(gap.product_id[:50] or None),
                        standard_question=gap.normalized_question,
                        standard_answer=saved_answer,
                        similar_questions=[gap.example_question],
                        service_stage="general",
                        risk_level="low",
                        retrieval_enabled=False,
                        auto_reply_eligible=False,
                        human_required=False,
                        status="draft",
                        review_status="pending_validation",
                        source=f"knowledge_gap:{gap.id}",
                        created_by="console",
                    )
                )
            else:
                saved_answer = existing.standard_answer
            gap.status = "draft"
            gap.candidate_answer = saved_answer
            gap.linked_qa_code = qa_code
            await session.commit()
            await session.refresh(gap)
            return knowledge_gap_to_dict(gap)

    async def shop_stats(
        self, shop_id: str, *, since: datetime
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            inbound_conversations = select(Message.conversation_id).where(
                Message.shop_id == shop_id,
                Message.direction == "inbound",
                Message.is_backfill.is_(False),
                Message.occurred_at >= since,
            ).distinct()
            consultation_count = int(
                await session.scalar(
                    select(func.count()).select_from(inbound_conversations.subquery())
                )
                or 0
            )
            ai_replies = int(
                await session.scalar(
                    select(func.count(OutboundJob.id)).where(
                        OutboundJob.shop_id == shop_id,
                        OutboundJob.source == "auto",
                        OutboundJob.status == "sent",
                        OutboundJob.sent_at >= since,
                    )
                )
                or 0
            )
            handoff_count = int(
                await session.scalar(
                    select(func.count(func.distinct(ReplyDecision.conversation_id))).where(
                        ReplyDecision.shop_id == shop_id,
                        ReplyDecision.action == "handoff",
                        ReplyDecision.created_at >= since,
                    )
                )
                or 0
            )
            gap_count = int(
                await session.scalar(
                    select(func.count(KnowledgeGap.id)).where(
                        KnowledgeGap.shop_id == shop_id,
                        KnowledgeGap.last_seen_at >= since,
                    )
                )
                or 0
            )
            sent_jobs = list(
                await session.scalars(
                    select(OutboundJob)
                    .where(
                        OutboundJob.shop_id == shop_id,
                        OutboundJob.status == "sent",
                        OutboundJob.sent_at >= since,
                    )
                    .order_by(OutboundJob.conversation_id, OutboundJob.sent_at)
                )
            )
            first_replies: dict[str, OutboundJob] = {}
            for job in sent_jobs:
                first_replies.setdefault(job.conversation_id, job)
            compliant = sum(
                1
                for job in first_replies.values()
                if job.sent_at is not None
                and job.response_deadline_at is not None
                and _aware_utc(job.sent_at) <= _aware_utc(job.response_deadline_at)
            )
            return {
                "consultation_conversations": consultation_count,
                "ai_replies": ai_replies,
                "handoff_rate": handoff_count / consultation_count
                if consultation_count
                else 0.0,
                "knowledge_gap_rate": gap_count / consultation_count
                if consultation_count
                else 0.0,
                "sla_compliance_rate": compliant / consultation_count
                if consultation_count
                else 0.0,
            }

    async def cleanup(self, message_days: int, audit_days: int) -> list[str]:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            message_cutoff = now - timedelta(days=message_days)
            audit_cutoff = now - timedelta(days=audit_days)
            expired_message_ids = select(Message.id).where(Message.created_at < message_cutoff)
            candidate_keys = set(
                await session.scalars(
                    select(MessageAsset.storage_key).where(
                        MessageAsset.message_id.in_(expired_message_ids),
                        MessageAsset.storage_key.is_not(None),
                    )
                )
            )
            await session.execute(
                delete(MessageAsset).where(MessageAsset.message_id.in_(expired_message_ids))
            )
            await session.execute(
                delete(Message).where(Message.created_at < message_cutoff)
            )
            await session.execute(
                update(ReplyDecision)
                .where(ReplyDecision.created_at < message_cutoff)
                .values(suggested_answer=None)
            )
            await session.execute(
                delete(ReplyDecision).where(ReplyDecision.created_at < audit_cutoff)
            )
            await session.execute(
                delete(OutboundJob).where(OutboundJob.created_at < audit_cutoff)
            )
            await session.execute(
                delete(ConnectorEvent).where(ConnectorEvent.created_at < audit_cutoff)
            )
            remaining_keys = set(
                await session.scalars(
                    select(MessageAsset.storage_key).where(
                        MessageAsset.storage_key.in_(candidate_keys)
                    )
                )
            ) if candidate_keys else set()
            await session.commit()
            return sorted(str(key) for key in candidate_keys - remaining_keys if key)
