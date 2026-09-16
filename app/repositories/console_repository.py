"""客服控制台的 MySQL/SQLAlchemy 持久化实现。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, desc, func, or_, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import NO_VALUE
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.pdd.base import normalize_platform_customer_id
from app.models.conversation import (
    AgentAccount,
    ConnectorEvent,
    Conversation,
    Customer,
    Message,
    MessageAsset,
    OutboundJob,
    ReplyDecision,
    Shop,
)


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


def conversation_to_dict(item: Conversation) -> dict[str, Any]:
    return {
        "id": item.id,
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
        "updated_at": _iso(item.updated_at),
    }


def message_to_dict(item: Message) -> dict[str, Any]:
    state = sa_inspect(item)
    loaded_assets = state.attrs.assets.loaded_value
    assets = [] if loaded_assets is NO_VALUE else loaded_assets
    return {
        "id": item.id,
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
        "created_at": _iso(item.created_at),
    }


def decision_to_dict(item: ReplyDecision | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "id": item.id,
        "route": item.route,
        "action": item.action,
        "qa_code": item.qa_code,
        "top_score": item.top_score,
        "score_margin": item.score_margin,
        "risk_reason": item.risk_reason,
        "suggested_answer": item.suggested_answer,
        "policy_version": item.policy_version,
        "created_at": _iso(item.created_at),
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
                item = Shop(name=name, platform="pdd")
                session.add(item)
                await session.commit()
                await session.refresh(item)
            return {
                "id": item.id,
                "name": item.name,
                "global_auto_reply_enabled": item.global_auto_reply_enabled,
            }

    async def get_shop(self, shop_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.get(Shop, shop_id)
            if item is None:
                return None
            return {
                "id": item.id,
                "name": item.name,
                "global_auto_reply_enabled": item.global_auto_reply_enabled,
            }

    async def set_global_automation(self, shop_id: str, enabled: bool) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = await session.get(Shop, shop_id)
            if item is None:
                raise LookupError("店铺不存在")
            item.global_auto_reply_enabled = enabled
            await session.commit()
            return {"enabled": enabled}

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
        self, shop_id: str, payload: dict[str, Any]
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
                    select(Message).options(selectinload(Message.assets)).where(Message.fingerprint == payload["fingerprint"])
                )
                if existing is None and payload.get("platform_message_id"):
                    same_platform = list(
                        await session.scalars(
                            select(Message).options(selectinload(Message.assets)).where(
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
                if payload["direction"] == "inbound" and not bool(
                    payload.get("is_backfill")
                ):
                    conversation.unread_count += 1
                    if conversation.state != "manual":
                        conversation.state = "pending"
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
            desc(Conversation.last_message_at), desc(Conversation.id)
        ).limit(limit)
        async with self._session_factory() as session:
            items = (await session.scalars(statement)).all()
            return [conversation_to_dict(item) for item in items]

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.get(Conversation, conversation_id)
            return conversation_to_dict(item) if item else None

    async def mark_conversation_read(self, conversation_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = await session.get(Conversation, conversation_id)
            if item is None:
                raise LookupError("会话不存在")
            item.unread_count = 0
            await session.commit()
            return conversation_to_dict(item)

    async def list_messages(
        self, conversation_id: str, *, limit: int, before: datetime | None = None
    ) -> list[dict[str, Any]]:
        statement = select(Message).options(selectinload(Message.assets)).where(
            Message.conversation_id == conversation_id,
            Message.assignment_verified.is_(True),
        )
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
    ) -> list[dict[str, Any]]:
        statement = select(Message).options(selectinload(Message.assets)).where(
            Message.customer_id == customer_id,
            Message.assignment_verified.is_(True),
        )
        if message_date:
            statement = statement.where(Message.message_date == message_date)
        statement = statement.order_by(desc(Message.occurred_at), desc(Message.id)).limit(limit)
        async with self._session_factory() as session:
            items = list(await session.scalars(statement))
            items.reverse()
            return [message_to_dict(item) for item in items]

    async def set_conversation_automation(
        self, conversation_id: str, enabled: bool
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = await session.get(Conversation, conversation_id)
            if item is None:
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
            existing = await session.scalar(
                select(OutboundJob).where(
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
                conversation_id=conversation_id,
                client_request_id=client_request_id,
                source=source,
                content=content,
            )
            session.add(job)
            if source == "manual":
                conversation = await session.get(Conversation, conversation_id)
                if conversation is None:
                    raise LookupError("会话不存在")
                conversation.auto_reply_enabled = False
                conversation.state = "manual"
                conversation.unread_count = 0
            else:
                conversation = await session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.last_outbound_status = "queued"
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(OutboundJob).where(
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

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.get(OutboundJob, job_id)
            return job_to_dict(item) if item else None

    async def recover_queued_jobs(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            # 进程可能在点击前后任一时刻退出，sending 无法证明未点击，只能转 uncertain。
            sending_conversations = list(
                await session.scalars(
                    select(OutboundJob.conversation_id).where(
                        OutboundJob.status == "sending"
                    )
                )
            )
            await session.execute(
                update(OutboundJob)
                .where(OutboundJob.status == "sending")
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
                    .where(OutboundJob.status == "queued")
                    .order_by(OutboundJob.created_at)
                )
            ).all()
            return [job_to_dict(item) for item in items]

    async def update_job(
        self,
        job_id: str,
        *,
        status: str,
        error_code: str | None = None,
        clicked_at: datetime | None = None,
        sent_at: datetime | None = None,
        responder_name: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            job = await session.get(OutboundJob, job_id)
            if job is None:
                raise LookupError("发送任务不存在")
            allowed = {
                "queued": {"sending", "failed"},
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
                    sender_type="automation" if job.source == "auto" else "agent",
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
            await session.commit()
            await session.refresh(job)
            return job_to_dict(job)

    async def add_decision(self, conversation_id: str, data: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(ReplyDecision).where(ReplyDecision.batch_key == data["batch_key"])
            )
            if existing:
                return decision_to_dict(existing) or {}
            item = ReplyDecision(conversation_id=conversation_id, **data)
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return decision_to_dict(item) or {}

    async def latest_decision(self, conversation_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.scalar(
                select(ReplyDecision)
                .where(ReplyDecision.conversation_id == conversation_id)
                .order_by(desc(ReplyDecision.created_at))
                .limit(1)
            )
            return decision_to_dict(item)

    async def add_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            item = ConnectorEvent(event_type=event_type, payload=payload)
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return {
                "id": item.id,
                "event_type": item.event_type,
                "payload": item.payload,
                "created_at": _iso(item.created_at),
            }

    async def events_after(self, event_id: int, limit: int = 200) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            items: Sequence[ConnectorEvent] = (
                await session.scalars(
                    select(ConnectorEvent)
                    .where(ConnectorEvent.id > event_id)
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

    async def latest_event_id(self) -> int:
        async with self._session_factory() as session:
            value = await session.scalar(select(func.max(ConnectorEvent.id)))
            return int(value or 0)

    async def get_message_asset(self, asset_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            item = await session.get(MessageAsset, asset_id)
            if item is None or not item.storage_key:
                return None
            return {
                "id": item.id,
                "storage_key": item.storage_key,
                "mime_type": item.mime_type or "application/octet-stream",
                "sha256": item.sha256,
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
