"""高风险售后执行请求规则；工具能力接入前先转人工。"""

from __future__ import annotations

import re

from app.rules.base import BaseRule, RuleContext, RuleDecision


_REQUEST_CUE = (
    r"(?:(?:^|[，,。！!？?\s])(?:请|麻烦|要求|办理|(?:请|麻烦)?(?:帮我|给我))"
    r"|(?:我要|我想|现在要))(?:申请|办理)?"
)
_REFUND_PATTERN = re.compile(
    _REQUEST_CUE + r"(?:仅退款|退款)|^(?:仅退款|退款|申请退款)$"
)
_RETURN_PATTERN = re.compile(
    _REQUEST_CUE + r"(?:退货|拒收)|^(?:退货|拒收|申请退货)$"
)
_EXCHANGE_PATTERN = re.compile(_REQUEST_CUE + r"换货|^换货$")
_CANCEL_ORDER_PATTERN = re.compile(
    _REQUEST_CUE + r"取消(?:这个|该|我的)?订单"
    r"|^取消(?:这个|该|我的)?订单$"
)
_COMPENSATION_PATTERN = re.compile(
    _REQUEST_CUE + r"(?:赔偿|补偿|赔付)|^(?:赔偿|补偿|赔付)$"
)
_PATTERNS = (
    ("refund_request", _REFUND_PATTERN),
    ("return_request", _RETURN_PATTERN),
    ("exchange_request", _EXCHANGE_PATTERN),
    ("cancel_order_request", _CANCEL_ORDER_PATTERN),
    ("compensation_request", _COMPENSATION_PATTERN),
)


class AfterSaleRiskRule(BaseRule):
    """识别明确售后动作请求，售后政策咨询不在此层终止。"""

    name = "AfterSaleRiskRule"
    priority = 90
    terminal = True

    def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
        del context
        for reason_code, pattern in _PATTERNS:
            if pattern.search(message):
                return RuleDecision(
                    matched=True,
                    rule_name=self.name,
                    terminal=True,
                    route="human",
                    reason_code=reason_code,
                    fixed_reply="好的，售后事项需要人工为您核实处理，请稍候。",
                    requires_human=True,
                )
        return self.no_match()
