from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ProductStatus = Literal["draft", "published", "offline"]
StockStatus = Literal["unknown", "in_stock", "low_stock", "out_of_stock"]
VariantStatus = Literal["active", "inactive"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class ProductFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    platform: str = Field(default="pdd", min_length=1, max_length=32)
    internal_code: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    brand: str | None = Field(default=None, max_length=200)
    category_name: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    selling_points: list[str] = Field(default_factory=list, max_length=30)
    specifications: dict[str, str] = Field(default_factory=dict)
    usage: str | None = Field(default=None, max_length=4000)
    suitable_for: str | None = Field(default=None, max_length=2000)
    warnings: str | None = Field(default=None, max_length=2000)
    after_sales_limits: str | None = Field(default=None, max_length=2000)
    compliance_notes: str | None = None
    status: ProductStatus = "draft"
    owner_name: str | None = Field(default=None, max_length=100)
    reviewed_by: str | None = Field(default=None, max_length=100)
    effective_at: datetime | None = None
    published_at: datetime | None = None

    @field_validator(
        "internal_code",
        "brand",
        "category_name",
        "model",
        "usage",
        "suitable_for",
        "warnings",
        "after_sales_limits",
        "compliance_notes",
        "owner_name",
        "reviewed_by",
    )
    @classmethod
    def empty_to_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("selling_points")
    @classmethod
    def clean_selling_points(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any(len(value) > 500 for value in cleaned):
            raise ValueError("单条卖点不能超过 500 个字符")
        return cleaned

    @field_validator("specifications")
    @classmethod
    def clean_specifications(cls, values: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, value in values.items():
            normalized_key = key.strip()
            normalized_value = value.strip()
            if not normalized_key or not normalized_value:
                continue
            if len(normalized_key) > 100 or len(normalized_value) > 1000:
                raise ValueError("规格名称不能超过 100 字，规格值不能超过 1000 字")
            cleaned[normalized_key] = normalized_value
        return cleaned


class ProductCreate(ProductFields):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")


class ProductUpdate(ProductFields):
    pass


class ProductRead(ProductCreate):
    created_at: datetime
    updated_at: datetime
    variant_count: int = 0


class ProductListResponse(BaseModel):
    items: list[ProductRead]
    total: int
    page: int
    page_size: int


class VariantFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=500)
    attributes: dict[str, str] = Field(default_factory=dict)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    price: Decimal | None = Field(default=None, ge=0, decimal_places=2, max_digits=12)
    stock_status: StockStatus = "unknown"
    stock_quantity: int | None = Field(default=None, ge=0)
    status: VariantStatus = "active"

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("attributes")
    @classmethod
    def clean_attributes(cls, values: dict[str, str]) -> dict[str, str]:
        return {
            key.strip(): value.strip()
            for key, value in values.items()
            if key.strip() and value.strip()
        }


class VariantCreate(VariantFields):
    sku_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")


class VariantUpdate(VariantFields):
    pass


class VariantRead(VariantCreate):
    product_id: str
    created_at: datetime
    updated_at: datetime


class DashboardStats(BaseModel):
    total: int
    published: int
    draft: int
    offline: int
    variants: int
