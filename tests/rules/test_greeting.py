from app.rules import GreetingRule, RuleContext


def test_matches_pure_greetings() -> None:
    rule = GreetingRule()

    for message in ("你好", "您好", "在吗", "hello", "早上好", "你好呀"):
        decision = rule.evaluate(message, RuleContext())

        assert decision.matched is True
        assert decision.terminal is True
        assert decision.route == "greeting"
        assert decision.reason_code == "simple_greeting"
        assert decision.fixed_reply == "您好，请问有什么可以帮您？"


def test_does_not_match_greeting_plus_business_request() -> None:
    rule = GreetingRule()

    for message in (
        "你好这个怎么用",
        "您好，我的快递呢",
        "在吗我要退款",
        "你好，请问这个材质是什么",
    ):
        decision = rule.evaluate(message, RuleContext())

        assert decision.matched is False
        assert decision.terminal is False
        assert decision.rule_name == "GreetingRule"
