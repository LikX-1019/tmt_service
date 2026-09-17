"""客服控制台 API 的输入输出模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.agent.greeting import GREETING_TYPES, normalize_greeting_phrase


class ConversationView(BaseModel):
    id: str
    platform_conversation_id: str
    customer_id: str | None = None
    display_name: str
    avatar_url: str | None = None
    goods_id: str | None = None
    goods_name: str | None = None
    goods_price: str | None = None
    goods_url: str | None = None
    state: str
    last_outbound_status: str | None = None
    auto_reply_enabled: bool
    unread_count: int
    last_message_at: datetime | None = None
    response_started_at: datetime | None = None
    response_deadline_at: datetime | None = None
    updated_at: datetime | None = None


class ShopSummaryView(BaseModel):
    id: str
    platform: str
    name: str


class MessageView(BaseModel):
    id: str
    direction: Literal["inbound", "outbound"]
    kind: str
    content: str | None = None
    occurred_at: datetime
    message_date: date | None = None
    sender_type: str | None = None
    sender_account_id: str | None = None
    sender_display_name: str | None = None
    is_backfill: bool
    outbound_job_id: str | None = None
    assets: list["MessageAssetView"] = Field(default_factory=list)


class MessageAssetView(BaseModel):
    id: str
    asset_type: Literal["image", "emoji", "goods_image", "video", "screenshot"] | str
    source_url: str | None = None
    storage_key: str | None = None
    mime_type: str | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ReplyDecisionView(BaseModel):
    id: str
    route: str
    action: str
    greeting_type: Literal["salutation", "availability", "thanks", "goodbye"] | None = None
    recognition_source: Literal["rule", "llm"] | None = None
    qa_code: str | None = None
    top_score: float | None = None
    score_margin: float | None = None
    risk_reason: str | None = None
    suggested_answer: str | None = None
    policy_version: str
    created_at: datetime


class OutboundJobView(BaseModel):
    id: str
    conversation_id: str
    client_request_id: str
    source: Literal["manual", "auto"]
    content: str
    status: Literal["queued", "sending", "sent", "uncertain", "failed"]
    error_code: str | None = None
    responder_account_id: str | None = None
    responder_display_name: str | None = None
    clicked_at: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime


class ConversationListData(BaseModel):
    items: list[ConversationView]
    next_cursor: datetime | None = None


class CustomerView(BaseModel):
    id: str
    platform_customer_id: str
    display_name: str
    avatar_url: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime


class CustomerListData(BaseModel):
    items: list[CustomerView]


class CustomerMessagesData(BaseModel):
    customer_id: str
    day: date | None = None
    items: list[MessageView]


class ConversationMessagesData(BaseModel):
    conversation: ConversationView
    messages: list[MessageView]
    decision: ReplyDecisionView | None = None
    next_cursor: datetime | None = None


class ReplyRequest(BaseModel):
    content: str = Field(min_length=1, max_length=400)
    client_request_id: str = Field(min_length=8, max_length=64, pattern=r"^[\w:.-]+$")

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("回复内容不能为空")
        return value


class AutomationUpdate(BaseModel):
    enabled: bool


class AutomationData(BaseModel):
    enabled: bool


class GreetingTriggerGroups(BaseModel):
    salutation: list[str] = Field(max_length=50)
    availability: list[str] = Field(max_length=50)
    thanks: list[str] = Field(max_length=50)
    goodbye: list[str] = Field(max_length=50)

    @field_validator("*")
    @classmethod
    def validate_phrases(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = raw_value.strip()
            normalized = normalize_greeting_phrase(value)
            if not normalized:
                continue
            if len(value) > 40:
                raise ValueError("单条触发语不能超过 40 个字符")
            if normalized not in seen:
                cleaned.append(value)
                seen.add(normalized)
        return cleaned


class GreetingReplyTemplates(BaseModel):
    salutation: list[str] = Field(max_length=10)
    availability: list[str] = Field(max_length=10)
    thanks: list[str] = Field(max_length=10)
    goodbye: list[str] = Field(max_length=10)

    @field_validator("*", mode="before")
    @classmethod
    def accept_legacy_single_reply(cls, value: object) -> object:
        """兼容旧接口和旧页面提交的单条回复话术。"""
        return [value] if isinstance(value, str) else value

    @field_validator("*")
    @classmethod
    def validate_replies(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw_value in values:
            value = raw_value.strip()
            if len(value) > 400:
                raise ValueError("单条回复话术不能超过 400 个字符")
            if value and value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            raise ValueError("回复话术不能为空")
        return cleaned


class GreetingAutomationUpdate(BaseModel):
    enabled: bool
    trigger_groups: GreetingTriggerGroups
    reply_templates: GreetingReplyTemplates

    @model_validator(mode="after")
    def reject_cross_group_duplicates(self) -> "GreetingAutomationUpdate":
        owners: dict[str, str] = {}
        groups = self.trigger_groups.model_dump()
        for greeting_type in GREETING_TYPES:
            for phrase in groups[greeting_type]:
                normalized = normalize_greeting_phrase(phrase)
                previous = owners.get(normalized)
                if previous is not None and previous != greeting_type:
                    raise ValueError(f"触发语“{phrase}”不能同时属于多个分类")
                owners[normalized] = greeting_type
        return self


class GreetingAutomationData(GreetingAutomationUpdate):
    updated_at: datetime | None = None


class ConnectorStatusData(BaseModel):
    status: Literal["stopped", "starting", "login_required", "ready", "degraded", "error"]
    detail: str | None = None
    changed_at: datetime | None = None
