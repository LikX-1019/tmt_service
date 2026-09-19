"""PostgreSQL 商品资料的受控读取与商品问答。"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Literal, Protocol

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings, get_settings
from app.factories.llm_factory import LLMFactory


class ProductLookupError(RuntimeError):
    """商品资料服务未返回可安全使用的资料。"""


class ProductNotFoundError(ProductLookupError):
    """商品不存在、未发布或已被下架。"""


class ProductVariantFact(BaseModel):
    """客服只读 SKU 摘要；不向顾客暴露具体库存数量。"""

    model_config = ConfigDict(extra="ignore")

    sku_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=500)
    attributes: dict[str, str] = Field(default_factory=dict)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    price: Decimal | None = Field(default=None, ge=0)
    stock_status: Literal["unknown", "in_stock", "low_stock", "out_of_stock"] = (
        "unknown"
    )


class ProductProfile(BaseModel):
    """商品 PostgreSQL 视口暴露的真实字段。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    platform: str | None = Field(default=None, max_length=32)
    internal_code: str | None = Field(default=None, max_length=64)
    brand: str | None = Field(default=None, max_length=200)
    category_name: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    selling_points: list[str] = Field(default_factory=list, max_length=30)
    specifications: dict[str, str] = Field(default_factory=dict)
    variants: list[ProductVariantFact] = Field(default_factory=list, max_length=50)
    usage: str | None = Field(default=None, max_length=4000)
    suitable_for: str | None = Field(default=None, max_length=2000)
    warnings: str | None = Field(default=None, max_length=2000)
    after_sales_limits: str | None = Field(default=None, max_length=2000)
    compliance_notes: str | None = Field(default=None)
    updated_at: str | None = Field(default=None, max_length=100)

    @field_validator(
        "id",
        "name",
        "summary",
        "platform",
        "internal_code",
        "brand",
        "category_name",
        "model",
        "usage",
        "suitable_for",
        "warnings",
        "after_sales_limits",
        "compliance_notes",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ProductSearchResult(BaseModel):
    """候选商品选择所需的 PostgreSQL 真实字段。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    internal_code: str | None = Field(default=None, max_length=64)
    specifications: dict[str, str] = Field(default_factory=dict)
    match_type: Literal["exact", "contains"]

    @field_validator("id", "name", "summary", "internal_code")
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

    async def search_products(self, query: str, limit: int = 5) -> list[ProductSearchResult]: ...


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


    async def search_products(self, query: str, limit: int = 5) -> list[ProductSearchResult]:
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
                    f"{base_url}/products",
                    params={"query": query, "limit": limit},
                    headers=headers,
                    timeout=self._settings.product_api_timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{base_url}/products",
                        params={"query": query, "limit": limit},
                        headers=headers,
                        timeout=self._settings.product_api_timeout_seconds,
                    )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("响应不是对象")
            items = payload.get("data")
            if not isinstance(items, list):
                raise ValueError("商品候选响应格式无效")
            return [ProductSearchResult.model_validate(item) for item in items]
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ProductLookupError("商品资料暂不可用") from exc


class ProductContextBuilder:
    """仅把数据库真实字段格式化为受约束的 LLM 上下文。"""

    @staticmethod
    def build(product: ProductProfile) -> str:
        sections: list[tuple[str, str]] = [
            ("商品ID", product.id),
            ("商品名称", product.name),
            ("商品介绍", product.summary),
        ]
        described_fields = (
            ("平台", product.platform),
            ("内部编码", product.internal_code),
            ("品牌", product.brand),
            ("分类", product.category_name),
            ("型号", product.model),
        )
        sections.extend((title, value) for title, value in described_fields if value)
        if product.selling_points:
            sections.append(("特点", "\n".join(f"- {item}" for item in product.selling_points)))
        if product.specifications:
            sections.append(
                ("规格", "\n".join(f"- {key}：{value}" for key, value in product.specifications.items()))
            )
        if product.variants:
            sections.append(
                (
                    "可选SKU",
                    "\n".join(
                        f"- {variant.name}（{variant.sku_id}）："
                        f"{'、'.join(f'{key}={value}' for key, value in variant.attributes.items()) or '规格未提供'}；"
                        f"价格={'未提供' if variant.price is None else f'{variant.currency} {variant.price:.2f}'}；"
                        f"库存状态={variant.stock_status}"
                        for variant in product.variants
                    ),
                )
            )
        optional_sections = (
            ("使用方法", product.usage),
            ("适合场景", product.suitable_for),
            ("注意事项", product.warnings),
            ("售后限制", product.after_sales_limits),
            ("合规说明", product.compliance_notes),
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
                        "你是电商商品客服。Product Context 是当前商品的唯一事实来源，"
                        "必须优先检查其中已经提供的结构化字段和说明后作答。\n"
                        "规则：\n"
                        "1. 他/它/这个/这款/该商品等指代，默认指向 Conversation Context "
                        "中的当前商品；商品已明确时不要要求用户重复 SKU 或商品名。\n"
                        "2. 商品型号与尺码是不同概念：型号字段是商品型号；规格中的尺码/大小/"
                        "S/M/L 等是可选尺码。用户问“几个型号/有哪些码/有 L 吗”时，"
                        "要结合两类字段回答，必要时明确区分。\n"
                        "3. 支持短句和口语，如“他有l”“几个码”“有大码吗”。先结合当前商品资料推断意图。\n"
                        "4. 已知资料能回答时必须直接回答，可列出尺码数量、包含关系或最大尺码；"
                        "不要因为问法与字段名不一致而要求补充信息。\n"
                        "5. 价格只能来自可选 SKU 的结构化价格；多 SKU 可列价格或区间，"
                        "无价格时明确说明当前商品资料未提供价格。\n"
                        "6. 只有 stock_status=in_stock 才能说可购买；不得虚构库存数量，"
                        "unknown 或 out_of_stock 时不能承诺有货。\n"
                        "7. 只有检查全部 Product Context 后仍无相关事实，才说明当前商品资料未提供；"
                        "不得虚构材质、规格、尺寸、功能、效果、库存或适用结论。\n"
                        "8. 不复述完整商品介绍，不展示内部字段名、JSON 或检索过程。\n"
                        "9. 售后、退款、换货、赔付、订单、物流问题必须标记 "
                        "contains_sensitive_or_after_sales=true。\n"
                        "10. 商品事实不明确或资料不足时标记 needs_clarification=true、"
                        "facts_supported=false，并用中文说明缺口。"
                    )
                ),
                HumanMessage(
                    content="Product Context（当前商品结构化事实，回答前必须完整检查）：\n"
                    + ProductContextBuilder.build(product)
                    + f"\n\nConversation Context：\n{conversation_context}"
                    + "\n当前商品指代解析：用户消息中的他/它/这个/这款/该商品，"
                    "在未提供其他商品候选时均指当前商品。"
                    + "\n\nCurrent User Message：\n" + query
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
