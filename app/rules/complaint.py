"""明确投诉、举报和维权请求规则。"""

from __future__ import annotations

import re

from app.rules.base import BaseRule, RuleContext, RuleDecision


_COMPLAINT_PATTERN = re.compile(
    r"^(?:我要|我想|请|麻烦|帮我|给我|要求|现在要)投诉"
    r"|^(?:投诉你们|投诉商家|投诉店铺|投诉平台)"
    r"|(?:^|[，,。！!？?\s])(?:我要|我想|请|麻烦|帮我|给我|要求|现在要)投诉"
)
_REPORT_PATTERN = re.compile(
    r"(?:^|[，,。！!？?\s])(?:我要|我想|请|麻烦|帮我|给我|要求|现在要)"
    r"(?:举报|曝光)(?:你们|商家|店铺|平台)?"
)
_PLATFORM_PATTERN = re.compile(
    r"^(?:要求|申请)平台介入$"
    r"|(?:^|[，,。！!？?\s])(?:请|麻烦|帮我|给我|要求|申请|我要找|找)"
    r"(?:拼多多)?平台(?:介入|处理|客服)?"
    r"|^(?:要求|申请|请|麻烦|帮我|给我|我要)?(?:打|拨打|向)?12315$"
    r"|(?:^|[，,。！!？?\s])(?:要求|申请|请|麻烦|帮我|给我|我要)?"
    r"(?:打|拨打|向)12315(?:投诉|举报|维权)?"
)
_CONSUMER_RIGHTS_PATTERN = re.compile(
    r"^(?:我要|我想|请|麻烦|帮我|给我|要求)?维权$"
    r"|(?:^|[，,。！!？?\s])(?:找|联系|向|我要找)消费者协会"
    r"|^消费者协会$"
    r"|欺骗消费者"
)


class ComplaintRule(BaseRule):
    """用户主动发起争议处理时转人工。"""

    name = "ComplaintRule"
    priority = 95
    terminal = True

    def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
        del context
        if _PLATFORM_PATTERN.search(message):
            reason_code = "platform_intervention"
        elif _CONSUMER_RIGHTS_PATTERN.search(message):
            reason_code = "consumer_rights"
        elif _REPORT_PATTERN.search(message):
            reason_code = "report"
        elif _COMPLAINT_PATTERN.search(message):
            reason_code = "complaint"
        else:
            return self.no_match()

        return RuleDecision(
            matched=True,
            rule_name=self.name,
            terminal=True,
            route="human",
            reason_code=reason_code,
            fixed_reply="好的，您的问题需要人工跟进处理，请稍候。",
            requires_human=True,
        )
