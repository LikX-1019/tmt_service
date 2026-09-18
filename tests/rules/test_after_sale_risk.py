import pytest

from app.rules import AfterSaleRiskRule, RuleContext


@pytest.mark.parametrize(
    ("message", "reason_code"),
    [
        ("我要退款", "refund_request"),
        ("帮我退货", "return_request"),
        ("我要换货", "exchange_request"),
        ("给我取消订单", "cancel_order_request"),
        ("我要赔偿", "compensation_request"),
        ("请帮我申请退款", "refund_request"),
        ("这个不合适我要退款", "refund_request"),
    ],
)
def test_matches_explicit_after_sale_action(
    message: str,
    reason_code: str,
) -> None:
    decision = AfterSaleRiskRule().evaluate(message, RuleContext())

    assert decision.matched is True
    assert decision.terminal is True
    assert decision.route == "human"
    assert decision.reason_code == reason_code
    assert decision.requires_human is True


@pytest.mark.parametrize(
    "message",
    [
        "支持退款吗",
        "退货政策是什么",
        "多久可以退款",
        "怎么申请退款",
        "取消订单政策是什么",
    ],
)
def test_does_not_match_after_sale_policy_question(message: str) -> None:
    decision = AfterSaleRiskRule().evaluate(message, RuleContext())

    assert decision.matched is False
    assert decision.requires_human is False
