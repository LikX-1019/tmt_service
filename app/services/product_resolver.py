"""Deterministic product identity parsing; the first version never asks an LLM to guess."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from collections.abc import Sequence
from urllib.parse import parse_qs, urlparse

from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class ProductResolution:
    product_id: str | None
    source: str


@dataclass(frozen=True, slots=True)
class ConversationProductReference:
    """最近会话商品的轻量引用，只用于历史商品消歧。"""

    product_id: str
    product_name: str


_ID = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"
_PRODUCT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_MESSAGE_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"(?:product[_ ]?id|商品\s*id)\s*[:：=]?\s*({_ID})", re.IGNORECASE),
    re.compile(rf"商品(?:\s+|[:：]\s*)({_ID})", re.IGNORECASE),
)
_LEADING_BARE_ID_PATTERN = re.compile(
    rf"^\s*({_ID})(?=\s|[，,。.！!？?:：；;]|$)"
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
_SIZE_TOKEN_PATTERN = re.compile(r"^(?:[sml]|xl|xxl|[23]xl)$", re.IGNORECASE)
_SIZE_ATTRIBUTE_PATTERN = re.compile(
    r"(?:几个|有哪些|什么|最大|最小).{0,4}(?:型号|尺码|码)"
    r"|型号|尺码|(?:有|有吗|有没有)[\s]*[smlxyz]",
    re.IGNORECASE,
)
_HISTORICAL_REFERENCE_PATTERN = re.compile(
    r"刚刚|刚才|前面|之前|上次|^那.{2,30}呢$"
)
_QUESTION_TAIL_PATTERN = re.compile(r"(?:我)?(?:能|可以|想要|想|要)$")
_ATTRIBUTE_ONLY_QUERY_PATTERN = re.compile(
    r"(?:有|没有|哪|几|个|哪些|什么|最大|最小|怎么|为什么|型号|尺码|码|[smlxyz])+",
    re.IGNORECASE,
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
    "型号",
    "尺码",
    "有哪些码",
    "几个码",
    "最大码",
    "最小码",
    "大码",
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
_BARE_ID_CONTEXT_MARKERS = (*_QUESTION_MARKERS, "商品", "产品", "介绍", "详情", "价格", "多少钱")
_GENERIC_WORDS = (
    "刚刚那个",
    "刚刚这个",
    "刚刚说的",
    "刚才那个",
    "前面那个",
    "前面说的",
    "这个",
    "那个",
    "这款",
    "那款",
    "这种",
    "这样一种",
    "它",
    "他",
    "该",
    "商品",
    "产品",
)
_GENERIC_NAME_PARTS = {"这个", "那个", "这款", "那款", "这种", "它", "他", "该", "商品", "产品"}
# Duration follow-ups such as “一天戴多久” contain a time expression, not a product name.
_GENERIC_DURATION_QUERY = re.compile(
    r"(?:每天|每次|平时|平常|通常|一般|长期|连续)?"
    r"(?:[0-9一二两三四五六七八九十]+(?:天|日|小时|分钟|次))?"
    r"(?:戴|用|使用|佩戴|穿|清洗|保养)?"
)
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

        match = _LEADING_BARE_ID_PATTERN.match(message)
        if match:
            candidate = match.group(1)
            remainder = _normalize_text(message[match.end() :])
            looks_like_id = (
                candidate.isdigit()
                or any(separator in candidate for separator in "._:-")
                or any("A" <= character <= "Z" for character in candidate)
            )
            if _SIZE_TOKEN_PATTERN.fullmatch(candidate):
                return None
            contextual_remainder = any(
                marker in remainder for marker in _BARE_ID_CONTEXT_MARKERS
            ) or bool(_SIZE_ATTRIBUTE_PATTERN.search(remainder))
            if looks_like_id and (not remainder or contextual_remainder):
                return cls._normalize_id(candidate)
        return None

    @staticmethod
    def has_historical_reference(message: str) -> bool:
        """是否包含“刚刚那个 / 那这个…呢”等历史回指线索。"""
        return bool(_HISTORICAL_REFERENCE_PATTERN.search(message))

    @staticmethod
    def _historical_product_id(
        message: str,
        recent_products: Sequence[ConversationProductReference],
    ) -> str | None:
        """从最近商品中解析“刚刚那个护腕”这类明确回指。"""
        if not recent_products or not _HISTORICAL_REFERENCE_PATTERN.search(message):
            return None
        normalized_message = _normalize_text(message)
        matched_ids: list[str] = []
        descriptors: list[str] = []
        for candidate in recent_products:
            name = _normalize_text(candidate.product_name)
            descriptor: str | None = None
            for size in range(min(len(name), len(normalized_message)), 1, -1):
                for start in range(0, len(name) - size + 1):
                    part = name[start : start + size]
                    if part in normalized_message and part not in _GENERIC_NAME_PARTS:
                        descriptor = part
                        break
                if descriptor is not None:
                    break
            if descriptor is not None and candidate.product_id not in matched_ids:
                matched_ids.append(candidate.product_id)
                descriptors.append(descriptor)
        return matched_ids[0] if len(matched_ids) == 1 else None

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
        if normalized and _GENERIC_DURATION_QUERY.fullmatch(normalized):
            return None
        for word in _GENERIC_WORDS:
            if normalized.startswith(word):
                normalized = normalized[len(word) :].strip()
            if normalized.endswith(word):
                normalized = normalized[: -len(word)].strip()
        normalized = _QUESTION_TAIL_PATTERN.sub("", normalized).strip("的了呢吧啊吗")
        if (
            not normalized
            or len(normalized) < 2
            or normalized in _GENERIC_WORDS
            or _ATTRIBUTE_ONLY_QUERY_PATTERN.fullmatch(normalized)
        ):
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
        recent_products: Sequence[ConversationProductReference] = (),
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
        historical_id = self._historical_product_id(message, recent_products)
        if historical_id is not None:
            return ProductResolution(historical_id, "history")
        conversation_id = self._normalize_id(conversation_product_id)
        if conversation_id is not None:
            return ProductResolution(conversation_id, "conversation")
        return ProductResolution(None, "none")
