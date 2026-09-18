"""PostgreSQL 商品资料的受控读取与商品问答。"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings, get_settings
from app.factories.llm_factory import LLMFactory


class ProductLookupError(RuntimeError):
    """商品资料服务未返回可安全使用的资料。"""


class ProductNotFoundError(ProductLookupError):
    """商品不存在、未发布或已被下架。"""


class ProductProfile(BaseModel):
    """商品 PostgreSQL 视口暴露的真实字段。"""

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

    @field_validator(
        "id", "name", "summary", "usage", "suitable_for", "warnings", "after_sales_limits"
    )
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
    """访问 commodity_management 暴露的 PostgreSQL 商品只读接口。"""

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
            if response.status_code == 404:
                raise ProductNotFoundError("未找到对应商品")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("响应不是对象")
            profile = ProductProfile.model_validate(payload.get("data", payload))
        except ProductNotFoundError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ProductLookupError("商品资料暂不可用") from exc
        if profile.id != product_id:
            raise ProductLookupError("商品资料与会话商品不一致")
        return profile


class ProductContextBuilder:
    """仅把数据库真实字段格式化为受约束的 LLM 上下文。"""

    @staticmethod
    def build(product: ProductProfile) -> str:
        sections: list[tuple[str, str]] = [
            ("商品ID", product.id),
            ("商品名称", product.name),
            ("商品介绍", product.summary),
        ]
        if product.selling_points:
            sections.append(("特点", "\n".join(f"- {item}" for item in product.selling_points)))
        if product.specifications:
            sections.append(
                ("规格", "\n".join(f"- {key}：{value}" for key, value in product.specifications.items()))
            )
        optional_sections = (
            ("使用方法", product.usage),
            ("适合场景", product.suitable_for),
            ("注意事项", product.warnings),
            ("售后限制", product.after_sales_limits),
            ("资料更新时间", product.updated_at),
        )
        sections.extend((title, value) for title, value in optional_sections if value)
        return "\n".join(f"{title}：\n{value}" for title, value in sections)


class ProductAnswerService:
    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def answer(
        self,
        query: str,
        product: ProductProfile,
        *,
        conversation_id: str | None = None,
    ) -> ProductAnswer:
        llm = self._llm or await asyncio.to_thread(
            LLMFactory.get_structured_llm, "product", ProductAnswer, 0.0
        )
        conversation_context = (
            f"当前会话已绑定商品 {product.id}。" if conversation_id else "本次请求未提供会话 ID。"
        )
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是商品客服助手。回答当前商品相关问题时，只能依据提供的 Product Context。\n"
                        "规则：\n"
                        "1. 不得虚构商品不存在的信息。\n"
                        "2. 不得自行补充未提供的材质、规格、尺寸、效果、功能等事实。\n"
                        "3. Product Context 没有相关信息时，应明确说明当前商品资料中没有该信息。\n"
                        "4. 优先回答用户具体问题，不机械复述完整商品介绍。\n"
                        "5. 不得使用其他商品的信息替代当前商品。\n"
                        "6. 商品数据库是当前商品事实的主要来源。\n"
                        "7. 售后、退款、换货、赔付、订单、物流问题必须标记 "
                        "contains_sensitive_or_after_sales=true。\n"
                        "8. 资料不足或商品不明确时标记 needs_clarification=true、facts_supported=false。"
                    )
                ),
                HumanMessage(
                    content="Product Context：\n"
                    + ProductContextBuilder.build(product)
                    + f"\n\nConversation Context：\n{conversation_context}"
                    + f"\n\nCurrent User Message：\n{query}"
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
