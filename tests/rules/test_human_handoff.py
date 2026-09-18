import pytest

from app.rules import HumanHandoffRule, RuleContext


@pytest.mark.parametrize(
    "message",
    [
        "我要人工客服",
        "帮我转人工",
        "找真人客服",
        "转人工",
        "我要人工",
        "我要跟人工说",
        "请人工处理",
        "叫客服来",
    ],
)
def test_matches_explicit_human_request(message: str) -> None:
    decision = HumanHandoffRule().evaluate(message, RuleContext())

    assert decision.matched is True
    assert decision.terminal is True
    assert decision.route == "human"
    assert decision.reason_code == "explicit_human_request"
    assert decision.requires_human is True


@pytest.mark.parametrize(
    "message",
    [
        "人工客服和机器人有什么区别",
        "人工客服是什么意思",
        "什么是真人客服",
    ],
)
def test_does_not_match_human_service_concept_question(message: str) -> None:
    decision = HumanHandoffRule().evaluate(message, RuleContext())

    assert decision.matched is False
    assert decision.requires_human is False
