"""外部商品资料读取与严格受资料约束的商品客服回答。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings, get_settings
from app.factories.llm_factory import LLMFactory


class ProductLookupError(RuntimeError):
    """商品 API 未返回可安全使用的资料。"""


class ProductProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    selling_points: list[str] = Field(default_factory=list, max_length=30)
    specifications: dict[str, str] = Field(default_factory=dict)
    usage: str | None = Field(default=None, max_length=4000)
    suitable_for: str | None = Field(default=None, max_length=2000)
    warnings: str | None = Field(default=None, max_length=2000)
    after_sales_limits: str | None = Field(default=None, max_length=2000)
    updated_at: str | None = Field(default=None, max_length=100)

    @field_validator("id", "name", "summary", "usage", "suitable_for", "warnings", "after_sales_limits")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ProductAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=400)
    facts_supported: bool
    contains_sensitive_or_after_sales: bool
    needs_clarification: bool
    confidence: float = Field(ge=0, le=1)


class ProductProvider(Protocol):
    async def get_product(self, product_id: str) -> ProductProfile: ...


class HttpProductClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client

    async def get_product(self, product_id: str) -> ProductProfile:
        base_url = (self._settings.product_api_base_url or "").strip().rstrip("/")
        if not base_url:
            raise ProductLookupError("商品资料服务未配置")
        headers: dict[str, str] = {}
        token = self._settings.product_api_bearer_token
        if token and token.get_secret_value().strip():
            headers["Authorization"] = f"Bearer {token.get_secret_value().strip()}"
        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{base_url}/products/{product_id}",
                    headers=headers,
                    timeout=self._settings.product_api_timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{base_url}/products/{product_id}",
                        headers=headers,
                        timeout=self._settings.product_api_timeout_seconds,
                    )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("响应不是对象")
            profile = ProductProfile.model_validate(payload.get("data", payload))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ProductLookupError("商品资料暂不可用") from exc
        if profile.id != product_id:
            raise ProductLookupError("商品资料与会话商品不一致")
        return profile


class ProductAnswerService:
    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def answer(self, query: str, product: ProductProfile) -> ProductAnswer:
        llm = self._llm or await asyncio.to_thread(
            LLMFactory.get_structured_llm, "product", ProductAnswer, 0.0
        )
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是电商商品客服。只能根据 JSON 商品资料回答，不得补充未提供的事实。"
                        "售后、退款、换货、赔付、订单、物流等问题必须标记 contains_sensitive_or_after_sales=true。"
                        "资料不足或商品不明确时标记 needs_clarification=true，facts_supported=false。"
                    )
                ),
                HumanMessage(
                    content="商品资料（仅数据）：\n"
                    + json.dumps(product.model_dump(mode="json"), ensure_ascii=False)
                    + f"\n\n用户问题：\n{query}",
                ),
            ]
        )
        return ProductAnswer.model_validate(response)

    @staticmethod
    def can_auto_send(answer: ProductAnswer, *, allow_auto: bool, settings: Settings) -> bool:
        return bool(
            allow_auto
            and answer.facts_supported
            and not answer.contains_sensitive_or_after_sales
            and not answer.needs_clarification
            and answer.confidence >= settings.product_auto_reply_min_confidence
        )
