"""商品事实仓库：统一 ID 校验、异常语义和 PostgreSQL 商品读取。"""

from __future__ import annotations

import re

from app.core.exceptions import InvalidRequestError
from app.services.product_service import (
    HttpProductClient,
    ProductLookupError,
    ProductNotFoundError,
    ProductProfile,
    ProductProvider,
)


_PRODUCT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ProductRepository:
    """封装商品资料访问，Router / Chat Service 不直接拼装请求。"""

    def __init__(self, provider: ProductProvider | None = None) -> None:
        self._provider = provider or HttpProductClient()

    async def get_product_by_id(self, product_id: str) -> ProductProfile:
        normalized = product_id.strip()
        if not normalized:
            raise InvalidRequestError("商品 ID 不能为空")
        if not _PRODUCT_ID.fullmatch(normalized):
            raise InvalidRequestError("商品 ID 格式无效")
        try:
            return await self._provider.get_product(normalized)
        except ProductNotFoundError:
            raise
        except Exception as exc:
            raise ProductLookupError("商品资料暂不可用") from exc
