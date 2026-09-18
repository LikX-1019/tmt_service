"""Deterministic product identity parsing; the first version never asks an LLM to guess."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class ProductResolution:
    product_id: str | None
    source: str


_ID = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"
_PRODUCT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_MESSAGE_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"(?:product[_ ]?id|商品\s*id)\s*[:：=]?\s*({_ID})", re.IGNORECASE),
    re.compile(rf"商品(?:\s+|[:：]\s*)({_ID})", re.IGNORECASE),
)
_SEARCH_PREFIXES = (
    "我想问一下",
    "我想看看",
    "想问一下",
    "想看看",
    "我想咨询",
    "我想了解",
    "请帮我看看",
    "帮我看看",
    "请问一下",
    "请问",
    "咨询",
)
_QUESTION_MARKERS = (
    "有什么区别",
    "有什么功能",
    "有什么特点",
    "适合什么",
    "适合跑步吗",
    "适合场景",
    "怎么使用",
    "怎么清洗",
    "怎么戴",
    "怎么洗",
    "怎么用",
    "保养",
    "佩戴",
    "使用",
    "清洗",
    "多久",
    "尺寸",
    "多大",
    "材质",
    "规格",
    "颜色",
    "注意事项",
    "特点",
    "卖点",
    "区别",
    "不合适",
    "适合",
)
_GENERIC_WORDS = ("这个", "那个", "这款", "那款", "这种", "这样一种", "它", "该", "商品", "产品")
_PRONOUN_PRODUCT_PREFIXES = (
    "那这个商品",
    "这个商品",
    "那个商品",
    "那款商品",
    "这款商品",
    "该商品",
    "它",
)
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", flags=re.IGNORECASE)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip(" ，,。.！!？?；;：:～~\"'“”‘’")


class ProductLinkResolver:
    """Only parse the product detail URL format owned by this deployment."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")

    def _known_host(self, url: str) -> bool:
        if not self._base_url:
            return False
        try:
            product_host = urlparse(self._base_url).netloc.casefold()
            return bool(product_host) and urlparse(url).netloc.casefold() == product_host
        except ValueError:
            return False

    def extract(self, message: str) -> str | None:
        for candidate in _URL_PATTERN.findall(message):
            if not self._known_host(candidate):
                continue
            try:
                parsed = urlparse(candidate)
            except ValueError:
                continue
            for key in ("product_id", "productId", "goods_id", "item_id"):
                values = parse_qs(parsed.query).get(key, [])
                if values and _PRODUCT_ID.fullmatch(values[0]):
                    return values[0]
            match = re.fullmatch(r"/products/([^/]+)/?", parsed.path)
            if match and _PRODUCT_ID.fullmatch(match.group(1)):
                return match.group(1)
        return None

    def has_unresolved_known_url(self, message: str) -> bool:
        return any(
            self._known_host(candidate)
            for candidate in _URL_PATTERN.findall(message)
        )

    @staticmethod
    def has_url(message: str) -> bool:
        return _URL_PATTERN.search(message) is not None


class ProductResolver:
    """Resolve IDs by request, known product URL, message ID, then conversation."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self._links = ProductLinkResolver(settings.product_api_base_url)

    @staticmethod
    def _normalize_id(value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None

    @classmethod
    def extract_from_message(cls, message: str) -> str | None:
        for pattern in _MESSAGE_ID_PATTERNS:
            match = pattern.search(message)
            if match:
                return cls._normalize_id(match.group(1))
        return None

    @staticmethod
    def extract_name_query(message: str, *, product_intent: bool) -> str | None:
        """Extract a conservative name query without calling an LLM."""
        normalized = _normalize_text(_URL_PATTERN.sub(" ", message))
        if not normalized:
            return None
        if any(normalized.startswith(prefix) for prefix in _PRONOUN_PRODUCT_PREFIXES):
            return None
        search_intent = any(normalized.startswith(prefix) for prefix in _SEARCH_PREFIXES)
        for prefix in _SEARCH_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()
                break
        cut_positions = [
            normalized.find(marker)
            for marker in _QUESTION_MARKERS
            if normalized.find(marker) >= 0
        ]
        inferred_product_name = bool(cut_positions and min(cut_positions) > 0)
        if not product_intent and not search_intent and not inferred_product_name:
            return None
        if cut_positions:
            normalized = normalized[: min(cut_positions)].strip()
        for word in _GENERIC_WORDS:
            if normalized.startswith(word):
                normalized = normalized[len(word) :].strip()
            if normalized.endswith(word):
                normalized = normalized[: -len(word)].strip()
        normalized = normalized.strip("的了呢吧啊")
        if not normalized or len(normalized) < 2 or normalized in _GENERIC_WORDS:
            return None
        if re.fullmatch(r"[?？。，,.!！:：;；~～]+", normalized):
            return None
        return normalized

    def has_unresolved_known_url(self, message: str) -> bool:
        return self._links.has_unresolved_known_url(message) and self._links.extract(message) is None

    def has_url(self, message: str) -> bool:
        return self._links.has_url(message)

    def resolve(
        self,
        *,
        request_product_id: str | None,
        message: str,
        conversation_product_id: str | None,
    ) -> ProductResolution:
        request_id = self._normalize_id(request_product_id)
        if request_id is not None:
            return ProductResolution(request_id, "request")
        url_id = self._links.extract(message)
        if url_id is not None:
            return ProductResolution(url_id, "url")
        message_id = self.extract_from_message(message)
        if message_id is not None:
            return ProductResolution(message_id, "message_id")
        conversation_id = self._normalize_id(conversation_product_id)
        if conversation_id is not None:
            return ProductResolution(conversation_id, "conversation")
        return ProductResolution(None, "none")
