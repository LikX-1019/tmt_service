import pytest

from app.rules import ProductContextRule, RuleContext


@pytest.mark.parametrize("message", ["这个怎么用", "这款怎么清洗", "它一天戴多久"])
def test_marks_implicit_product_reference(message: str) -> None:
    decision = ProductContextRule().evaluate(message, RuleContext(current_product_id="p1"))

    assert decision.matched is True
    assert decision.terminal is False
    assert decision.requires_product is True
    assert decision.reason_code == "implicit_product_reference"
    assert decision.metadata == {"current_product_available": True}


@pytest.mark.parametrize("message", ["怎么用", "这个多少钱", "退款政策是什么"])
def test_does_not_mark_without_reference_and_product_question(message: str) -> None:
    decision = ProductContextRule().evaluate(message, RuleContext())

    assert decision.matched is False
    assert decision.requires_product is False


def test_bound_product_duration_question_uses_conversation_context() -> None:
    decision = ProductContextRule().evaluate("一天戴多久", RuleContext(current_product_id="p1"))

    assert decision.matched is True
    assert decision.requires_product is True
    assert decision.reason_code == "implicit_product_reference"
    assert decision.metadata == {"current_product_available": True}


def test_duration_question_without_bound_product_does_not_require_product() -> None:
    decision = ProductContextRule().evaluate("一天戴多久", RuleContext())

    assert decision.matched is False
    assert decision.requires_product is False


def test_metadata_reports_missing_current_product() -> None:
    decision = ProductContextRule().evaluate("这个怎么戴", RuleContext())

    assert decision.matched is True
    assert decision.metadata == {"current_product_available": False}
