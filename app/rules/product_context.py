"""当前商品依赖识别的上下文增强规则。"""

from __future__ import annotations

from app.rules.base import BaseRule, RuleContext, RuleDecision


_PRODUCT_REFERENCES = (
    "这个产品",
    "这个东西",
    "这款",
    "这一个",
    "这种",
    "这个",
    "它",
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
    "材质",
    "规格",
    "颜色",
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
        )
        if not (has_reference and has_product_question):
            return self.no_match()

        available = context.current_product_id is not None
        return RuleDecision(
            matched=True,
            rule_name=self.name,
            terminal=False,
            reason_code="implicit_product_reference",
            requires_product=True,
            metadata={"current_product_available": available},
        )
