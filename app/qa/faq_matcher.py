"""标准问法与显式别名的内存精确匹配器。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.qa.models import FAQItem
from app.qa.normalizer import QueryNormalizer


@dataclass(slots=True)
class FAQMatch:
    item: FAQItem | None = None
    match_score: float | None = None
    ambiguous: bool = False
    reason: str | None = None
    candidates: list[FAQItem] = field(default_factory=list)


def _context_value(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_name(value: str | None) -> str:
    return re.sub(r"\W+", "", (value or "").lower())


def _name_compatible(item_name: object, context_name: str) -> bool:
    left = _normalize_name(str(item_name or ""))
    right = _normalize_name(context_name)
    return bool(left and right and (left in right or right in left))


class FAQMatcher:
    def __init__(
        self,
        items: Iterable[FAQItem],
        normalizer: QueryNormalizer | None = None,
    ) -> None:
        self._normalizer = normalizer or QueryNormalizer()
        self._index: dict[str, list[FAQItem]] = {}
        for item in items:
            if not item.enabled:
                continue
            for variant in (item.question, *item.aliases):
                normalized = self._normalizer.normalize(variant)
                if normalized:
                    bucket = self._index.setdefault(normalized, [])
                    if all(existing.id != item.id for existing in bucket):
                        bucket.append(item)

    def resolve(
        self,
        query: str,
        *,
        product_code: str | None = None,
        service_stage: str | None = None,
        knowledge_version: str | None = None,
        product_name: str | None = None,
    ) -> FAQMatch:
        """按商品、阶段和版本解析 exact match；同优先级多条视为歧义。"""
        candidates = list(self._index.get(self._normalizer.normalize(query), []))
        if not candidates:
            return FAQMatch(reason="not_found")

        product = _context_value(product_code)
        stage = _context_value(service_stage) or "general"
        version = _context_value(knowledge_version)
        compatible = []
        for item in candidates:
            item_version = _context_value(item.metadata.get("applicable_version"))
            if version and item_version and version != item_version:
                continue
            compatible.append(item)

        priorities: list[tuple[str, str]] = []
        if product:
            priorities.extend([(product, stage), (product, "general")])
        priorities.extend([("", stage), ("", "general")])
        seen: set[tuple[str, str]] = set()
        for wanted in priorities:
            if wanted in seen:
                continue
            seen.add(wanted)
            matched = [
                item
                for item in compatible
                if (
                    _context_value(item.metadata.get("product_id")),
                    _context_value(item.metadata.get("service_stage")) or "general",
                )
                == wanted
            ]
            if len(matched) == 1:
                return FAQMatch(item=matched[0], match_score=1.0, candidates=matched)
            if len(matched) > 1:
                return FAQMatch(
                    match_score=1.0,
                    ambiguous=True,
                    reason="ambiguous_exact_match",
                    candidates=matched,
                )

        # 商品编码唯一命中时允许阶段缺省：拼多多连接器只能稳定拿到商品卡片，
        # 无法可靠判定售前/售后；同一商品同问法唯一时优先于通用知识。
        if product:
            matched = [
                item
                for item in compatible
                if _context_value(item.metadata.get("product_id")) == product
            ]
            if len(matched) == 1:
                return FAQMatch(item=matched[0], match_score=1.0, candidates=matched)
            if len(matched) > 1:
                return FAQMatch(
                    match_score=1.0,
                    ambiguous=True,
                    reason="ambiguous_exact_match",
                    candidates=matched,
                )

        # 平台数字商品 ID 与内部编码不一致时，用商品名称做双向包含匹配；
        # 多条命中仍视为歧义，不会任选一条。
        if product_name:
            matched = [
                item
                for item in compatible
                if _name_compatible(item.metadata.get("product_name"), product_name)
            ]
            if len(matched) == 1:
                return FAQMatch(item=matched[0], match_score=1.0, candidates=matched)
            if len(matched) > 1:
                return FAQMatch(
                    match_score=1.0,
                    ambiguous=True,
                    reason="ambiguous_exact_match",
                    candidates=matched,
                )

        reason = "missing_product_context" if not product and any(
            _context_value(item.metadata.get("product_id")) for item in compatible
        ) else "context_mismatch"
        return FAQMatch(match_score=1.0, reason=reason, candidates=compatible)

    def match(
        self,
        query: str,
        *,
        product_code: str | None = None,
        service_stage: str | None = None,
        knowledge_version: str | None = None,
        product_name: str | None = None,
    ) -> FAQItem | None:
        return self.resolve(
            query,
            product_code=product_code,
            service_stage=service_stage,
            knowledge_version=knowledge_version,
            product_name=product_name,
        ).item

    def __len__(self) -> int:
        return sum(len(items) for items in self._index.values())
