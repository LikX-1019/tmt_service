"""店铺问候配置、文本规范化与规则匹配。"""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from hashlib import sha256
from typing import Literal, Mapping, Sequence


GreetingType = Literal["salutation", "availability", "thanks", "goodbye"]
GREETING_TYPES: tuple[GreetingType, ...] = (
    "salutation",
    "availability",
    "thanks",
    "goodbye",
)

DEFAULT_TRIGGER_GROUPS: dict[GreetingType, list[str]] = {
    "salutation": ["你好", "您好", "哈喽", "嗨"],
    "availability": ["在吗", "有人吗", "客服在吗"],
    "thanks": ["谢谢", "辛苦了", "感谢"],
    "goodbye": ["再见", "拜拜", "先这样"],
}

DEFAULT_REPLY_TEMPLATES: dict[GreetingType, list[str]] = {
    "salutation": [
        "您好，亲，请问有什么可以帮您？",
        "您好呀，亲，有什么问题都可以告诉我。",
        "亲，您好，我在这里，请问需要了解什么呢？",
        "您好，欢迎咨询，请问有什么可以为您解答？",
        "您好呀，很高兴为您服务，请问您想咨询什么？",
    ],
    "availability": [
        "您好，亲，我在的，请问有什么可以帮您？",
        "在的，亲，您有什么问题可以直接告诉我。",
        "您好，我在线的，请问您想咨询什么呢？",
        "亲，在呢，有什么需要我帮您看看的吗？",
        "我在的，您请说，我马上帮您处理。",
    ],
    "thanks": [
        "不客气，亲，很高兴能帮到您。",
        "不用客气，这是我们应该做的。",
        "亲，不客气，能帮到您就好。",
        "感谢您的认可，有需要随时联系我们。",
        "不客气呀，祝您购物愉快！",
    ],
    "goodbye": [
        "好的，亲，感谢您的咨询，祝您生活愉快！",
        "好的，有需要随时联系我们，祝您生活愉快！",
        "感谢您的咨询，祝您每天都有好心情！",
        "好的，亲，那就先不打扰您啦，祝您一切顺利！",
        "很高兴为您服务，期待下次再见！",
    ],
}

_TRAILING_PUNCTUATION = re.compile(r"[\s\u3000。！？!?，,；;：:～~…\.]+$")
_LEADING_PUNCTUATION = re.compile(r"^[\s\u3000。！？!?，,；;：:～~…\.]+")
_GREETING_VOCATIVES = frozenset({"亲", "亲亲", "店家", "客服", "老板", "商家"})


def default_trigger_groups() -> dict[GreetingType, list[str]]:
    """返回可安全修改的默认触发语副本。"""
    return deepcopy(DEFAULT_TRIGGER_GROUPS)


def default_reply_templates() -> dict[GreetingType, list[str]]:
    """返回可安全修改的默认回复副本。"""
    return deepcopy(DEFAULT_REPLY_TEMPLATES)


def normalize_reply_templates(
    templates: Mapping[str, object] | None,
) -> dict[GreetingType, list[str]]:
    """兼容历史单条话术，并把旧默认值自动扩展为多版本。"""
    source = templates or {}
    normalized: dict[GreetingType, list[str]] = {}
    for greeting_type in GREETING_TYPES:
        defaults = DEFAULT_REPLY_TEMPLATES[greeting_type]
        raw_value = source.get(greeting_type, defaults)
        if isinstance(raw_value, str):
            values = [raw_value]
            if raw_value.strip() == defaults[0]:
                values = defaults
        elif isinstance(raw_value, Sequence):
            values = [str(value) for value in raw_value]
        else:
            values = defaults

        cleaned: list[str] = []
        seen: set[str] = set()
        for raw_reply in values:
            reply = raw_reply.strip()
            if reply and reply not in seen:
                cleaned.append(reply)
                seen.add(reply)
        normalized[greeting_type] = cleaned or list(defaults)
    return normalized


def select_reply_template(
    templates: Mapping[str, object] | None,
    greeting_type: GreetingType,
    *,
    selection_key: str,
) -> str:
    """按消息批次稳定分配话术，重试不换文案，新消息可重新随机分配。"""
    variants = normalize_reply_templates(templates)[greeting_type]
    digest = sha256(f"{selection_key}:{greeting_type}".encode()).digest()
    index = int.from_bytes(digest[:8], "big") % len(variants)
    return variants[index]


def normalize_greeting_phrase(value: str) -> str:
    """规范化短问候；只清理形式差异，不删除正文内部标点。"""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return _TRAILING_PUNCTUATION.sub("", normalized).strip()


def match_greeting_rule(
    message: str,
    trigger_groups: Mapping[str, Sequence[str]],
) -> GreetingType | None:
    """按完整规范化短语匹配问候分类，避免业务消息被子串误判。"""
    normalized_message = normalize_greeting_phrase(message)
    if not normalized_message:
        return None
    for greeting_type in GREETING_TYPES:
        phrases = trigger_groups.get(greeting_type, ())
        for phrase in phrases:
            normalized_phrase = normalize_greeting_phrase(str(phrase))
            if normalized_message == normalized_phrase:
                return greeting_type
            if greeting_type not in {"salutation", "availability"}:
                continue
            if not normalized_phrase or not normalized_message.startswith(
                normalized_phrase
            ):
                continue
            suffix = _LEADING_PUNCTUATION.sub(
                "", normalized_message[len(normalized_phrase) :]
            )
            if suffix in _GREETING_VOCATIVES:
                return greeting_type
    return None
