"""规则注册、排序、归一化和执行编排。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.rules.after_sale_risk import AfterSaleRiskRule
from app.rules.base import BaseRule, RuleContext, RuleDecision
from app.rules.complaint import ComplaintRule
from app.rules.courtesy import CourtesyRule
from app.rules.greeting import GreetingRule
from app.rules.human_handoff import HumanHandoffRule
from app.rules.product_context import ProductContextRule


_TRAILING_PUNCTUATION = re.compile(r"[。，,！!？?．.；;：:～~…\s]+$")


def normalize_message(raw_message: str) -> str:
    """只清理形式差异，不删除数字、型号或正文内部语义。"""
    normalized = unicodedata.normalize("NFKC", raw_message).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return _TRAILING_PUNCTUATION.sub("", normalized).strip()


class RuleEvaluationResult(BaseModel):
    """一次消息规则执行的聚合结果。"""

    model_config = ConfigDict(extra="forbid")

    raw_message: str
    normalized_message: str
    decisions: list[RuleDecision] = Field(default_factory=list)
    terminal_decision: RuleDecision | None = None
    requires_product: bool = False
    requires_human: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuleRegistry:
    """按优先级执行规则，并聚合 Enrichment 与 Terminal 结果。"""

    def __init__(self, rules: Iterable[BaseRule] | None = None) -> None:
        self._rules: list[BaseRule] = []
        for rule in rules or ():
            self.register(rule)

    def register(self, rule: BaseRule) -> None:
        if any(rule.name == registered.name for registered in self._rules):
            raise ValueError(f"Rule already registered: {rule.name}")
        self._rules.append(rule)
        self._rules.sort(key=lambda item: (-item.priority, item.name))

    @property
    def rules(self) -> tuple[BaseRule, ...]:
        return tuple(self._rules)

    def evaluate(
        self,
        message: str,
        context: RuleContext | None = None,
    ) -> RuleEvaluationResult:
        normalized = normalize_message(message)
        safe_context = context if context is not None else RuleContext()
        matched_decisions: list[RuleDecision] = []
        metadata: dict[str, Any] = {}
        terminal_decision: RuleDecision | None = None

        # Enrichment 先完整收集，避免高风险 Terminal 决策丢失商品上下文。
        for rule in (item for item in self._rules if not item.terminal):
            decision = rule.evaluate(normalized, safe_context)
            if not decision.matched:
                continue
            matched_decisions.append(decision)
            metadata.update(decision.metadata)

        # Terminal 规则按 priority 降序执行；高风险冲突先命中先停止。
        for rule in (item for item in self._rules if item.terminal):
            decision = rule.evaluate(normalized, safe_context)
            if not decision.matched:
                continue
            matched_decisions.append(decision)
            terminal_decision = decision
            break

        return RuleEvaluationResult(
            raw_message=message,
            normalized_message=normalized,
            decisions=matched_decisions,
            terminal_decision=terminal_decision,
            requires_product=any(
                decision.requires_product for decision in matched_decisions
            ),
            requires_human=any(
                decision.requires_human for decision in matched_decisions
            ),
            metadata=metadata,
        )


def default_rule_registry() -> RuleRegistry:
    """创建包含 v1 默认规则且可独立修改的 Registry。"""
    return RuleRegistry(
        [
            HumanHandoffRule(),
            ComplaintRule(),
            AfterSaleRiskRule(),
            ProductContextRule(),
            GreetingRule(),
            CourtesyRule(),
        ]
    )
