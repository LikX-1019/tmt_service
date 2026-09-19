import pytest

from app.rules import SocialRule, RuleContext


@pytest.mark.parametrize(
    ("message", "route", "reason_code"),
    [
        ("下次见", "small_talk", "social_farewell"),
        ("回头见", "small_talk", "social_farewell"),
        ("我爱你", "small_talk", "social_affection"),
        ("我很生气", "empathy", "social_empathy"),
        ("我很着急", "empathy", "social_empathy"),
    ],
)
def test_social_rule_replies_without_knowledge_fallback(
    message: str, route: str, reason_code: str
) -> None:
    decision = SocialRule().evaluate(message, RuleContext())

    assert decision.matched is True
    assert decision.terminal is True
    assert decision.route == route
    assert decision.reason_code == reason_code
    assert decision.fixed_reply is not None
    assert decision.fixed_reply != "目前知识库中暂无相关信息，请联系人工客服获取帮助。"


def test_business_message_does_not_use_social_rule() -> None:
    decision = SocialRule().evaluate("我很生气，商品坏了", RuleContext())

    assert decision.matched is False
