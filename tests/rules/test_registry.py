from typing import Any

import pytest
from pydantic import ValidationError

from app.rules import (
    RuleContext,
    RuleRegistry,
    default_rule_registry,
    normalize_message,
)
from app.rules.base import BaseRule, RuleDecision
from app.rules.courtesy import CourtesyRule
from app.rules.greeting import GreetingRule


def test_normalize_keeps_semantic_content() -> None:
    assert normalize_message("  你好！！　") == "你好"
    assert normalize_message("订单  10086-A") == "订单 10086-a"
    assert normalize_message("HELLO？") == "hello"


def test_default_rules_are_sorted_and_serializable() -> None:
    registry = default_rule_registry()

    assert [rule.name for rule in registry.rules] == [
        "HumanHandoffRule",
        "ComplaintRule",
        "AfterSaleRiskRule",
        "ProductContextRule",
        "GreetingRule",
        "CourtesyRule",
    ]
    result = registry.evaluate("这个怎么戴", RuleContext())
    payload: dict[str, Any] = result.model_dump(mode="json")
    assert payload["decisions"][0]["rule_name"] == "ProductContextRule"
    assert payload["requires_product"] is True


@pytest.mark.parametrize(
    ("message", "rule_name", "route"),
    [
        ("你好，我要投诉", "ComplaintRule", "human"),
        ("您好，我要人工", "HumanHandoffRule", "human"),
        ("这个不合适我要退款", "AfterSaleRiskRule", "human"),
        ("你好", "GreetingRule", "greeting"),
        ("谢谢", "CourtesyRule", "fallback"),
    ],
)
def test_terminal_priority_resolves_conflicts(
    message: str,
    rule_name: str,
    route: str,
) -> None:
    result = default_rule_registry().evaluate(message, RuleContext())

    assert result.terminal_decision is not None
    assert result.terminal_decision.rule_name == rule_name
    assert result.terminal_decision.route == route
    assert result.requires_human == (route == "human")


def test_enrichment_is_kept_with_terminal_after_sale_request() -> None:
    result = default_rule_registry().evaluate(
        "这个不合适我要退款",
        RuleContext(current_product_id="p1"),
    )

    assert [decision.rule_name for decision in result.decisions] == [
        "ProductContextRule",
        "AfterSaleRiskRule",
    ]
    assert result.terminal_decision is not None
    assert result.terminal_decision.route == "human"
    assert result.requires_product is True
    assert result.requires_human is True
    assert result.metadata == {"current_product_available": True}


def test_no_terminal_result_continues_to_future_router() -> None:
    result = default_rule_registry().evaluate("这个怎么戴", RuleContext())

    assert result.terminal_decision is None
    assert result.normalized_message == "这个怎么戴"
    assert result.requires_product is True
    assert result.requires_human is False


def test_registry_rejects_duplicate_rule_names() -> None:
    registry = RuleRegistry([GreetingRule()])
    with pytest.raises(ValueError, match="Rule already registered"):
        registry.register(GreetingRule())


def test_rule_context_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RuleContext.model_validate({"unexpected": "value"})


def test_custom_rule_uses_base_contract_without_special_casing() -> None:
    class UpperCaseRule(BaseRule):
        name = "UpperCaseRule"
        priority = 1000
        terminal = True

        def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
            if message == "upper":
                return RuleDecision(
                    matched=True,
                    rule_name=self.name,
                    terminal=True,
                    route="fallback",
                    reason_code="test_upper",
                )
            return self.no_match()

    result = RuleRegistry([UpperCaseRule()]).evaluate("upper", RuleContext())
    assert result.terminal_decision is not None
    assert result.terminal_decision.rule_name == "UpperCaseRule"


def test_independent_registry_instances_do_not_share_rules() -> None:
    first = RuleRegistry([GreetingRule()])
    second = RuleRegistry([CourtesyRule()])

    assert len(first.rules) == 1
    assert len(second.rules) == 1
    assert first.rules[0].name != second.rules[0].name
