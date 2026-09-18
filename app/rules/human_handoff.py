"""明确人工请求的高置信度规则。"""

from __future__ import annotations

import re

from app.rules.base import BaseRule, RuleContext, RuleDecision


_EXPLICIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:^|[，,。！!？?\s])(?:请|麻烦|帮我|给我|我要|我想|需要|能不能|可以)?"
        r"(?:转接|转|找|叫)(?:人工|真人)(?:客服)?"
    ),
    re.compile(
        r"(?:^|[，,。！!？?\s])(?:我要|我想|需要)(?:人工|真人)(?:客服)?$"
    ),
    re.compile(r"^(?:人工|真人)(?:客服|处理)$"),
    re.compile(
        r"(?:^|[，,。！!？?\s])(?:请|麻烦|帮我|给我|我要|我想|需要|要求)"
        r"人工处理"
    ),
    re.compile(
        r"(?:^|[，,。！!？?\s])(?:我要|我想)?(?:跟|和)(?:人工|真人)(?:客服)?说$"
    ),
    re.compile(r"(?:^|[，,。！!？?\s])让真人(?:客服)?来$"),
    re.compile(r"(?:^|[，,。！!？?\s])叫(?:人工)?客服(?:来|过来)$"),
)


class HumanHandoffRule(BaseRule):
    """识别用户明确要求人工，而不是讨论人工客服概念。"""

    name = "HumanHandoffRule"
    priority = 100
    terminal = True

    def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
        del context
        if any(pattern.search(message) for pattern in _EXPLICIT_PATTERNS):
            return RuleDecision(
                matched=True,
                rule_name=self.name,
                terminal=True,
                route="human",
                reason_code="explicit_human_request",
                fixed_reply="好的，这边为您转接人工客服，请稍候。",
                requires_human=True,
            )
        return self.no_match()
