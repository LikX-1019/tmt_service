import pytest

from app.rules import CourtesyRule, RuleContext


@pytest.mark.parametrize(
    ("message", "reason_code"),
    [
        ("谢谢", "thanks"),
        ("好的谢谢", "thanks"),
        ("非常感谢", "thanks"),
        ("好的", "acknowledgement"),
        ("知道了", "acknowledgement"),
        ("ok", "acknowledgement"),
        ("再见", "farewell"),
        ("谢谢再见", "farewell"),
    ],
)
def test_matches_pure_courtesy(message: str, reason_code: str) -> None:
    decision = CourtesyRule().evaluate(message, RuleContext())

    assert decision.matched is True
    assert decision.terminal is True
    assert decision.reason_code == reason_code
    assert decision.fixed_reply is not None


@pytest.mark.parametrize(
    "message",
    [
        "谢谢，那多久洗一次",
        "好的，我还想问个问题",
        "再见之前问一下尺码",
        "知道了，但是退款什么时候到",
    ],
)
def test_does_not_match_courtesy_with_business_question(message: str) -> None:
    decision = CourtesyRule().evaluate(message, RuleContext())

    assert decision.matched is False
    assert decision.terminal is False
