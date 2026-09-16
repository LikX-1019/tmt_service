"""首条可运行的客服 Agent 路径：意图识别后处理日常问候。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.core.exceptions import AppException, LLMInvocationError
from app.factories.llm_factory import LLMFactory
from app.prompts.chat import build_customer_service_system_prompt
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
    """意图模型必须返回的最小结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["daily_greeting", "other"]
    confidence: float = Field(ge=0, le=1)


class AgentReply(BaseModel):
    """Agent 路由结果；未支持的意图不生成答案。"""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["daily_greeting", "other"]
    confidence: float = Field(ge=0, le=1)
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
        response_llm: Any | None = None,
    ) -> None:
        self._intent_llm = intent_llm
        self._response_llm = response_llm

    async def run(self, message: str, customer: CustomerContext) -> AgentReply:
        """先识别意图；只有日常问候会继续调用回复模型。"""
        decision = await self._recognize_intent(message)
        logger.info(
            "agent_intent_recognized",
            extra={
                "event": "agent_intent_recognized",
                "intent": decision.intent,
                "confidence": decision.confidence,
            },
        )
        if decision.intent != "daily_greeting":
            return AgentReply(
                intent=decision.intent,
                confidence=decision.confidence,
            )

        answer = await self._generate_greeting(message, customer)
        return AgentReply(
            intent=decision.intent,
            confidence=decision.confidence,
            answer=answer,
        )

    async def _recognize_intent(self, message: str) -> IntentDecision:
        try:
            llm = self._intent_llm or await asyncio.to_thread(
                LLMFactory.get_structured_llm,
                "intent",
                IntentDecision,
            )
            result = await llm.ainvoke(
                [
                    SystemMessage(content=GREETING_INTENT_SYSTEM_PROMPT),
                    HumanMessage(content=message),
                ]
            )
            return IntentDecision.model_validate(result)
        except AppException:
            raise
        except Exception as exc:
            logger.exception(
                "agent_intent_failed",
                extra={"event": "agent_intent_failed"},
            )
            raise LLMInvocationError() from exc

    async def _generate_greeting(
        self, message: str, customer: CustomerContext
    ) -> str:
        settings = get_settings()
        try:
            llm = self._response_llm or await asyncio.to_thread(
                LLMFactory.get_llm,
                "response",
                settings.llm_temperature,
            )
            result = await llm.ainvoke(
                [
                    SystemMessage(
                        content=build_customer_service_system_prompt(
                            customer.prompt_context()
                        )
                    ),
                    HumanMessage(content=message),
                ]
            )
            answer = _content_to_text(result.content)
            if not answer:
                raise RuntimeError("LLM returned empty content")
            return answer
        except AppException:
            raise
        except Exception as exc:
            logger.exception(
                "agent_greeting_response_failed",
                extra={"event": "agent_greeting_response_failed"},
            )
            raise LLMInvocationError() from exc
