"""规则优先、模型兜底的一般问候识别与固定话术回复。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.greeting import (
    GreetingType,
    default_trigger_groups,
    match_greeting_rule,
    select_reply_template,
)
from app.factories.llm_factory import LLMFactory
from app.prompts.intent import GREETING_INTENT_SYSTEM_PROMPT


logger = logging.getLogger(__name__)


class CustomerContext(BaseModel):
    """中台传给 Agent 的会话身份；只有必要展示字段会进入模型提示词。"""

    model_config = ConfigDict(extra="forbid")

    customer_id: str | None = None
    platform_customer_id: str | None = None
    display_name: str | None = None
    shop_id: str | None = None
    shop_name: str | None = None
    goods_id: str | None = None
    goods_name: str | None = None

    def prompt_context(self) -> dict[str, str | None]:
        """仅暴露生成自然称呼所需的资料，不把稳定标识发送给模型。"""
        return {
            "shop_name": self.shop_name,
            "customer_display_name": self.display_name,
            "goods_name": self.goods_name,
        }


class IntentDecision(BaseModel):
    """意图模型必须返回的严格 JSON 结果。"""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["daily_greeting", "other"]
    greeting_type: GreetingType | None = None
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_greeting_type(self) -> "IntentDecision":
        if self.intent == "daily_greeting" and self.greeting_type is None:
            raise ValueError("daily_greeting 必须提供 greeting_type")
        if self.intent == "other" and self.greeting_type is not None:
            raise ValueError("other 的 greeting_type 必须为空")
        return self


class AgentReply(BaseModel):
    """Agent 路由结果；未支持的意图不生成答案。"""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["daily_greeting", "other"]
    confidence: float = Field(ge=0, le=1)
    greeting_type: GreetingType | None = None
    recognition_source: Literal["rule", "llm"] | None = None
    answer: str | None = None


def _content_to_text(content: Any) -> str:
    """兼容 LangChain 字符串和文本块响应。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts).strip()
    return str(content).strip() if content is not None else ""


class CustomerServiceAgent:
    """识别用户意图，并为当前已支持的日常问候路径生成回复。"""

    def __init__(
        self,
        *,
        intent_llm: Any | None = None,
    ) -> None:
        self._intent_llm = intent_llm

    async def run(
        self,
        message: str,
        customer: CustomerContext,
        *,
        greeting_config: Mapping[str, Any] | None = None,
    ) -> AgentReply:
        """规则优先识别问候；模型只兜底分类，回复始终来自店铺配置。"""
        # 固定话术不把顾客资料发送给模型，但用稳定顾客标识分配话术版本。
        config = greeting_config or {
            "enabled": True,
            "trigger_groups": default_trigger_groups(),
            "reply_templates": {},
        }
        if not bool(config.get("enabled", True)):
            return AgentReply(intent="other", confidence=1.0)

        trigger_groups = {
            **default_trigger_groups(),
            **(config.get("trigger_groups") or {}),
        }
        reply_templates = config.get("reply_templates")
        selection_key = (
            customer.platform_customer_id
            or customer.customer_id
            or customer.display_name
            or message
        )
        rule_type = match_greeting_rule(message, trigger_groups)
        if rule_type is not None:
            return AgentReply(
                intent="daily_greeting",
                greeting_type=rule_type,
                confidence=1.0,
                recognition_source="rule",
                answer=select_reply_template(
                    reply_templates,
                    rule_type,
                    selection_key=selection_key,
                ),
            )

        decision = await self._recognize_intent(message)
        logger.info(
            "agent_intent_recognized",
            extra={
                "event": "agent_intent_recognized",
                "intent": decision.intent,
                "greeting_type": decision.greeting_type,
                "confidence": decision.confidence,
            },
        )
        if (
            decision.intent != "daily_greeting"
            or decision.greeting_type is None
            or decision.confidence < 0.9
        ):
            return AgentReply(
                intent="other",
                confidence=decision.confidence,
                recognition_source="llm",
            )

        return AgentReply(
            intent=decision.intent,
            greeting_type=decision.greeting_type,
            confidence=decision.confidence,
            recognition_source="llm",
            answer=select_reply_template(
                reply_templates,
                decision.greeting_type,
                selection_key=selection_key,
            ),
        )

    async def _recognize_intent(self, message: str) -> IntentDecision:
        try:
            llm = self._intent_llm or await asyncio.to_thread(
                LLMFactory.get_llm, "intent", 0.0
            )
            result = await llm.ainvoke(
                [
                    SystemMessage(content=GREETING_INTENT_SYSTEM_PROMPT),
                    HumanMessage(content=message),
                ]
            )
            if isinstance(result, IntentDecision):
                return result
            content = _content_to_text(getattr(result, "content", result))
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(lines[1:-1]).strip()
            return IntentDecision.model_validate(json.loads(content))
        except Exception:
            logger.exception(
                "agent_intent_failed",
                extra={"event": "agent_intent_failed"},
            )
            return IntentDecision(intent="other", confidence=0.0)
