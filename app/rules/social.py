"""短寒暄、喜爱表达和负面情绪的确定性兜底规则。"""

from __future__ import annotations

import re

from app.rules.base import BaseRule, RuleContext, RuleDecision


_PARTICLES = "呀啊哦哈呢啦吧的了～~！!。"


def _clean_message(message: str) -> str:
    return message.strip().strip(_PARTICLES).strip()


_FAREWELL_PATTERN = re.compile(r"^(?:下次见|回头见|改天见|再见|拜拜)$")
_AFFECTION_PATTERN = re.compile(r"^(?:我)?(?:很|超级|特别|真的)?(?:爱你|喜欢你)$")
_EMPATHY_PATTERN = re.compile(
    r"^(?:我)?(?:现在|真的)?(?:很|太|特别|超级|好)?"
    r"(?:生气|不满意|着急|失望|郁闷|烦躁|烦)$"
)


class SocialRule(BaseRule):
    """只处理无业务诉求的短社交表达，避免进入知识库 fallback。"""

    name = "SocialRule"
    priority = 45
    terminal = True

    def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
        del context
        phrase = _clean_message(message)
        if _FAREWELL_PATTERN.fullmatch(phrase):
            reason_code = "social_farewell"
            route = "small_talk"
            reply = "好的，下次再见，祝您生活愉快～"
        elif _AFFECTION_PATTERN.fullmatch(phrase):
            reason_code = "social_affection"
            route = "small_talk"
            reply = "谢谢您的认可～请问有什么可以帮您？"
        elif _EMPATHY_PATTERN.fullmatch(phrase):
            reason_code = "social_empathy"
            route = "empathy"
            reply = (
                "非常抱歉让您有不愉快的体验。请告诉我具体问题或订单号，"
                "我会尽力帮您处理。"
            )
        else:
            return self.no_match()

        return RuleDecision(
            matched=True,
            rule_name=self.name,
            terminal=True,
            route=route,
            reason_code=reason_code,
            fixed_reply=reply,
        )
