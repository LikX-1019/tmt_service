"""当前商品依赖识别的上下文增强规则。"""

from __future__ import annotations

import re

from app.rules.base import BaseRule, RuleContext, RuleDecision


_PRODUCT_REFERENCES = (
    "这个产品",
    "这个东西",
    "这款",
    "这一个",
    "这种",
    "这个",
    "它",
    "他",
    "刚刚那个",
    "刚刚这个",
    "刚刚说的",
    "刚才那个",
    "前面那个",
    "前面说的",
)
_PRODUCT_SWITCH_PATTERN = re.compile(r"^那.{2,30}呢$")
_PRODUCT_ATTRIBUTE_PATTERN = re.compile(
    r"(?:几个|有哪些|什么|最大|最小).{0,4}(?:型号|尺码|码)"
    r"|型号|尺码|(?:有|有吗|有没有)[\s]*[smlxyz]",
    re.IGNORECASE,
)
_PRODUCT_QUESTION_HINTS = (
    "不合适",
    "有什么区别",
    "有什么功能",
    "适合什么",
    "适合跑步吗",
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
    "材质",
    "规格",
    "颜色",
    "防水",
    "特点",
    "卖点",
    "注意事项",
)


class ProductContextRule(BaseRule):
    """标记明显依赖当前商品的咨询，但不终止后续路由。"""

    name = "ProductContextRule"
    priority = 60
    terminal = False

    def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
        has_reference = any(reference in message for reference in _PRODUCT_REFERENCES)
        has_product_question = any(
            hint in message for hint in _PRODUCT_QUESTION_HINTS
        ) or bool(
            _PRODUCT_ATTRIBUTE_PATTERN.search(message)
            or _PRODUCT_SWITCH_PATTERN.search(message)
        )
        has_bound_product = context.current_product_id is not None
        if not has_product_question or not (has_reference or has_bound_product):
            return self.no_match()

        available = has_bound_product
        return RuleDecision(
            matched=True,
            rule_name=self.name,
            terminal=False,
            reason_code="implicit_product_reference",
            requires_product=True,
            metadata={"current_product_available": available},
        )
