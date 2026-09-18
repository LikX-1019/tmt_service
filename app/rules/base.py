"""Rule Engine 的基础模型和统一规则接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import ChatRoute, ServiceStage


class RuleContext(BaseModel):
    """规则执行所需的最小上下文，不携带完整状态或业务对象。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    customer_id: str | None = None
    shop_id: str | None = None
    current_product_id: str | None = Field(default=None, min_length=1)
    service_stage: ServiceStage | None = None
    channel: str | None = None


class RuleDecision(BaseModel):
    """单条规则的稳定、可序列化判断结果。"""

    model_config = ConfigDict(extra="forbid")

    matched: bool = False
    rule_name: str | None = None
    terminal: bool = False
    route: ChatRoute | None = None
    reason_code: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    fixed_reply: str | None = None
    requires_human: bool = False
    requires_product: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseRule(ABC):
    """所有客服规则的统一同步接口。"""

    name: ClassVar[str]
    priority: ClassVar[int]
    terminal: ClassVar[bool]

    @abstractmethod
    def evaluate(self, message: str, context: RuleContext) -> RuleDecision:
        """对已规范化消息返回一个 ``RuleDecision``。"""

    def no_match(self) -> RuleDecision:
        """返回带规则名的未命中结果。"""
        return RuleDecision(rule_name=self.name, terminal=False)
