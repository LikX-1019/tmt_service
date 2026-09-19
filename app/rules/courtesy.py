"""感谢、确认和结束对话等纯礼貌用语规则。"""

from __future__ import annotations

import re

from app.rules.base import BaseRule, RuleContext, RuleDecision


_FAREWELL_PHRASES = frozenset(
    {
        "再见",
        "拜拜",
        "下次见",
        "回头见",
        "改天见",
        "没事了",
        "没有问题了",
        "先这样",
        "谢谢再见",
        "好的再见",
        "那先这样",
    }
)
_THANKS_PHRASES = frozenset(
    {
        "谢谢",
        "感谢",
        "多谢",
        "谢谢你",
        "感谢你",
        "非常感谢",
        "太感谢了",
        "辛苦了",
        "好的谢谢",
        "好的,谢谢",
        "好的，谢谢",
    }
)
_ACKNOWLEDGEMENT_PHRASES = frozenset(
    {
        "好的",
        "好的好的",
        "知道了",
        "明白了",
        "了解了",
        "行",
        "可以",
        "ok",
        "okay",
        "嗯",
        "嗯嗯",
        "收到",
    }
)
_PARTICLES = "呀哦哈呢啊啦吧"
_PUNCTUATION = "。，,！!？?。．..；;：:～~\u3000"
_PHRASE_SEPARATORS = re.compile(r"[，,！!？?。．.\s]+")


def _clean_phrase(message: str) -> str:
    value = message.strip(_PUNCTUATION)
    value = _PHRASE_SEPARATORS.sub("，", value)
    while value and value[-1] in _PARTICLES:
        value = value[:-1].rstrip(_PUNCTUATION)
    return value


class CourtesyRule(BaseRule):
    """只允许短、明确、无额外业务问题的礼貌表达命中。"""

    name = "CourtesyRule"
    priority = 40
    terminal = True

    def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
        del context
        phrase = _clean_phrase(message)
        if phrase in _FAREWELL_PHRASES:
            reason_code = "farewell"
            fixed_reply = "好的，祝您生活愉快，有需要随时联系我们～"
        elif phrase in _THANKS_PHRASES:
            reason_code = "thanks"
            fixed_reply = "不客气，有其他问题也可以继续问我～"
        elif phrase in _ACKNOWLEDGEMENT_PHRASES:
            reason_code = "acknowledgement"
            fixed_reply = "好的，有其他问题随时告诉我～"
        else:
            return self.no_match()

        return RuleDecision(
            matched=True,
            rule_name=self.name,
            terminal=True,
            route="small_talk",
            reason_code=reason_code,
            fixed_reply=fixed_reply,
        )
