"""统一聊天接口的请求、响应和调试模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ServiceStage = Literal["pre_sale", "post_sale", "general"]
ProductResolutionSource = Literal["request", "message", "conversation", "none"]


class ChatRequest(BaseModel):
    """聊天请求：conversation 与显式商品上下文均为可选。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    customer_id: str | None = Field(default=None, min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    product_id: str | None = Field(default=None, min_length=1, max_length=64)
    service_stage: ServiceStage | None = None


class ChatProductView(BaseModel):
    id: str
    name: str


class ChatSourceView(BaseModel):
    chunk_id: str | None = None
    title: str | None = None
    source: str | None = None
    score: float | None = None


class ChatResponse(BaseModel):
    """包含路由来源、商品上下文和可观测信息的业务响应。"""

    conversation_id: str | None = None
    answer: str
    source: Literal["rule", "product", "qa"]
    route: str | None = None
    product: ChatProductView | None = None
    product_resolution: ProductResolutionSource = "none"
    rule_name: str | None = None
    reason_code: str | None = None
    confidence: float | None = None
    sources: list[ChatSourceView] = Field(default_factory=list)
