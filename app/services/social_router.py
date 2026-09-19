"""社交/情绪意图路由：确定性规则优先，仅短文本走 LLM 兜底。"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.factories.llm_factory import LLMFactory
from app.rules.base import RuleContext
from app.rules.social import SocialRule


SocialIntent = Literal["small_talk", "empathy", "other"]


class SocialDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: SocialIntent = "other"
    response: str | None = None
    reason_code: str | None = None
    source: Literal["rule", "llm"] | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class _LLMSocialDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: SocialIntent = "other"
    confidence: float = Field(default=0.0, ge=0, le=1)


_BUSINESS_CUE_PATTERN = re.compile(
    r"退款|退货|换货|订单|物流|发货|库存|价格|多少钱|尺码|型号|材质|商品|产品|"
    r"怎么|什么|哪|吗|呢|适合|介绍|使用|购买|"
    r"投诉|举报|赔付|赔偿|人工|链接|SKU|TEST-|\d{4,}",
    re.IGNORECASE,
)
_GENERIC_SOCIAL_RESPONSES = {
    "small_talk": "谢谢您的关注～请问有什么可以帮您？",
    "empathy": (
        "非常抱歉让您有不愉快的体验。请告诉我具体问题或订单号，"
        "我会尽力帮您处理。"
    ),
}


class SocialRouter:
    """统一 Chat 和 Console 共用的社交意图识别入口。"""

    def __init__(self, llm: Any | None = None) -> None:
        self._rule = SocialRule()
        self._llm = llm

    async def classify(self, message: str) -> SocialDecision:
        normalized = " ".join(message.split())
        decision = self._rule.evaluate(normalized, RuleContext())
        if decision.matched and decision.terminal:
            assert decision.route in {"small_talk", "empathy"}
            return SocialDecision(
                intent=decision.route,
                response=decision.fixed_reply,
                reason_code=decision.reason_code,
                source="rule",
                confidence=decision.confidence,
            )
        if len(normalized) > 20 or _BUSINESS_CUE_PATTERN.search(normalized):
            return SocialDecision()

        try:
            llm = self._llm or await asyncio.to_thread(
                LLMFactory.get_structured_llm, "intent", _LLMSocialDecision, 0.0
            )
            result = await llm.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "你是电商客服意图分类器。只判断独立的短寒暄或情绪表达。"
                            "small_talk 表示感谢、告别、喜爱等社交表达；"
                            "empathy 表示顾客表达不满、着急、失望等情绪；"
                            "other 表示有商品、订单、售后或其他业务诉求。"
                            "只输出 JSON 字段 intent 和 confidence。"
                        )
                    ),
                    HumanMessage(content=normalized),
                ]
            )
            parsed = (
                result
                if isinstance(result, _LLMSocialDecision)
                else _LLMSocialDecision.model_validate(result)
            )
        except Exception:
            return SocialDecision()
        if parsed.intent == "other" or parsed.confidence < 0.8:
            return SocialDecision()
        return SocialDecision(
            intent=parsed.intent,
            response=_GENERIC_SOCIAL_RESPONSES[parsed.intent],
            reason_code=f"social_{parsed.intent}_llm_fallback",
            source="llm",
            confidence=parsed.confidence,
        )
