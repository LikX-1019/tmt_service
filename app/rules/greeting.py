"""纯问候规则，避免把带业务诉求的消息提前截断。"""

from __future__ import annotations

import re

from app.rules.base import BaseRule, RuleContext, RuleDecision


_GREETINGS = (
    "hello",
    "hi",
    "早上好",
    "下午好",
    "晚上好",
    "你好",
    "您好",
    "哈喽",
    "在吗",
    "有人吗",
    "嗨",
    "早",
)
_GREETING_PATTERN = re.compile(
    r"^(?:亲)?(?:" + "|".join(_GREETINGS) + r")(?:亲)?[呀哦哈呢啊啦]?$"
)


class GreetingRule(BaseRule):
    """只处理短、纯问候，不根据子串误判业务消息。"""

    name = "GreetingRule"
    priority = 50
    terminal = True

    def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
        del context
        if _GREETING_PATTERN.fullmatch(message):
            return RuleDecision(
                matched=True,
                rule_name=self.name,
                terminal=True,
                route="greeting",
                reason_code="simple_greeting",
                fixed_reply="您好，请问有什么可以帮您？",
            )
        return self.no_match()
