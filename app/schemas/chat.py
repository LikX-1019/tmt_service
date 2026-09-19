"""统一聊天接口的请求、响应和调试模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ServiceStage = Literal["pre_sale", "post_sale", "general"]
ProductResolutionSource = Literal[
    "request",
    "url",
    "message_id",
    "name_exact",
    "name_unique_contains",
    "history",
    "history_semantic",
    "name_candidates",
    "conversation",
    "none",
]


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


class ChatProductCandidateView(ChatProductView):
    summary: str
    internal_code: str | None = None
    specifications: dict[str, str] = Field(default_factory=dict)


class ChatSourceView(BaseModel):
    chunk_id: str | None = None
    title: str | None = None
    source: str | None = None
    score: float | None = None


class ChatResponse(BaseModel):
    """包含路由来源、商品上下文和可观测信息的业务响应。"""

    conversation_id: str | None = None
    answer: str
    source: Literal[
        "rule", "product", "product_selection", "qa", "llm_fallback"
    ]
    route: str | None = None
    product: ChatProductView | None = None
    products: list[ChatProductCandidateView] = Field(default_factory=list)
    product_resolution: ProductResolutionSource = "none"
    rule_name: str | None = None
    reason_code: str | None = None
    confidence: float | None = None
    qa_hit: bool | None = None
    sources: list[ChatSourceView] = Field(default_factory=list)
