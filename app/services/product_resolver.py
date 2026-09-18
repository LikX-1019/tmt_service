"""确定性 product_id 解析组件；第一版不使用 LLM 猜测。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductResolution:
    product_id: str | None
    source: str


_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"(?:product[_ ]?id|商品\s*id)\s*[:：=]?\s*({_ID})", re.IGNORECASE),
    re.compile(rf"商品\s*[:：]?\s*({_ID})", re.IGNORECASE),
)


class ProductResolver:
    """按 request、message、conversation 的优先级解析商品 ID。"""

    @staticmethod
    def _normalize(value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None

    @classmethod
    def extract_from_message(cls, message: str) -> str | None:
        for pattern in _PATTERNS:
            match = pattern.search(message)
            if match:
                return cls._normalize(match.group(1))
        return None

    def resolve(
        self,
        *,
        request_product_id: str | None,
        message: str,
        conversation_product_id: str | None,
    ) -> ProductResolution:
        request_id = self._normalize(request_product_id)
        if request_id is not None:
            return ProductResolution(request_id, "request")
        message_id = self.extract_from_message(message)
        if message_id is not None:
            return ProductResolution(message_id, "message")
        conversation_id = self._normalize(conversation_product_id)
        if conversation_id is not None:
            return ProductResolution(conversation_id, "conversation")
        return ProductResolution(None, "none")
