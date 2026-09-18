import pytest

from app.rules import ComplaintRule, RuleContext


@pytest.mark.parametrize(
    ("message", "reason_code"),
    [
        ("我要投诉", "complaint"),
        ("投诉商家", "complaint"),
        ("我要举报", "report"),
        ("我要曝光", "report"),
        ("我要找平台介入", "platform_intervention"),
        ("我要打12315投诉", "platform_intervention"),
        ("向12315投诉这个商家", "platform_intervention"),
        ("我要维权", "consumer_rights"),
        ("欺骗消费者", "consumer_rights"),
    ],
)
def test_matches_explicit_complaint(message: str, reason_code: str) -> None:
    decision = ComplaintRule().evaluate(message, RuleContext())

    assert decision.matched is True
    assert decision.terminal is True
    assert decision.route == "human"
    assert decision.reason_code == reason_code
    assert decision.requires_human is True


@pytest.mark.parametrize(
    "message",
    ["12315是什么", "这个产品投诉多吗", "平台介入是什么意思", "维权是什么"],
)
def test_does_not_match_complaint_concept_question(message: str) -> None:
    decision = ComplaintRule().evaluate(message, RuleContext())

    assert decision.matched is False
    assert decision.requires_human is False
