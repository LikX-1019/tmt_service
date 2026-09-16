"""高精度 FAQ 匹配使用的轻量查询标准化。"""

from __future__ import annotations

import re
import unicodedata


_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[\u3000，。！？；：、“”‘’（）【】《》〈〉〔〕…—～,.!?;:'\"`()\[\]{}<>~@#$%^&*_+=|\\/-]+")


class QueryNormalizer:
    def normalize(self, query: str) -> str:
        normalized = unicodedata.normalize("NFKC", query).strip().lower()
        normalized = _PUNCTUATION.sub("", normalized)
        return _WHITESPACE.sub("", normalized)
