"""Customer Service Rules v1：Router 前置的高确定性纯规则层。"""

from app.rules.after_sale_risk import AfterSaleRiskRule
from app.rules.base import BaseRule, RuleContext, RuleDecision
from app.rules.complaint import ComplaintRule
from app.rules.courtesy import CourtesyRule
from app.rules.greeting import GreetingRule
from app.rules.human_handoff import HumanHandoffRule
from app.rules.social import SocialRule
from app.rules.product_context import ProductContextRule
from app.rules.registry import (
    RuleEvaluationResult,
    RuleRegistry,
    default_rule_registry,
    normalize_message,
)

__all__ = [
    "AfterSaleRiskRule",
    "BaseRule",
    "ComplaintRule",
    "CourtesyRule",
    "GreetingRule",
    "HumanHandoffRule",
    "SocialRule",
    "ProductContextRule",
    "RuleContext",
    "RuleDecision",
    "RuleEvaluationResult",
    "RuleRegistry",
    "default_rule_registry",
    "normalize_message",
]
