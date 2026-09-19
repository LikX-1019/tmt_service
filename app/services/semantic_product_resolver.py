"""受限的语义化历史商品解析。

确定性规则无法处理“水杯 → 运动水壶”这类口语别名时，用 LLM 在最近商品候选内做受控消歧。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.factories.llm_factory import LLMFactory
from app.services.product_resolver import ConversationProductReference


class SemanticProductResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched: bool = False
    product_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason_code: Literal[
        "semantic_match",
        "ambiguous",
        "no_match",
        "low_confidence",
        "resolver_unavailable",
    ] = "no_match"
    ambiguous: bool = False


class _LLMSemanticProductResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    ambiguous: bool = False


class SemanticProductResolver:
    """只允许在最近商品候选中解析别名和历史回指。"""

    def __init__(self, llm: Any | None = None, *, min_confidence: float = 0.75) -> None:
        self._llm = llm
        self._min_confidence = min_confidence

    @staticmethod
    def _format_candidates(
        candidates: Sequence[ConversationProductReference],
        current_product_id: str | None,
    ) -> str:
        return "\n".join(
            f"{index}. product_id={candidate.product_id}; "
            f"name={candidate.product_name}"
            + ("; [当前绑定]" if candidate.product_id == current_product_id else "")
            for index, candidate in enumerate(candidates, start=1)
        )

    def _build_prompt(
        self,
        message: str,
        candidates: Sequence[ConversationProductReference],
        current_product_id: str | None,
    ) -> str:
        return (
            "请判断用户这句话中的商品指代对应哪个最近商品候选。\n"
            "规则：\n"
            "1. 只能返回候选列表中的 product_id；禁止编造其他商品。\n"
            "2. 可以理解常见口语别名，例如“水杯”对应“运动水壶”，“垫子”对应“瑜伽垫”。\n"
            "3. 如果多个候选都高度匹配，ambiguous=true，不要随意选择。\n"
            "4. 如果没有明确商品指代或没有候选匹配，product_id=null 且 ambiguous=false。\n"
            "5. 只输出 JSON 字段 product_id、confidence、ambiguous。\n\n"
            f"当前绑定商品ID：{current_product_id or '无'}\n"
            "最近商品候选：\n"
            f"{self._format_candidates(candidates, current_product_id)}\n\n"
            f"用户句子：{message}"
        )

    async def resolve(
        self,
        message: str,
        *,
        candidates: Sequence[ConversationProductReference],
        current_product_id: str | None = None,
    ) -> SemanticProductResolution:
        if not candidates:
            return SemanticProductResolution(reason_code="no_match")
        try:
            llm = self._llm or await asyncio.to_thread(
                LLMFactory.get_structured_llm,
                "response",
                _LLMSemanticProductResolution,
                0.0,
            )
            raw = await llm.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "你是电商客服的多轮商品指代解析器。"
                            "只做最近商品候选范围内的受控消歧，不回答业务问题。"
                        )
                    ),
                    HumanMessage(
                        content=self._build_prompt(
                            message, candidates, current_product_id
                        )
                    ),
                ]
            )
            parsed = (
                raw
                if isinstance(raw, _LLMSemanticProductResolution)
                else _LLMSemanticProductResolution.model_validate(raw)
            )
        except Exception:
            return SemanticProductResolution(reason_code="resolver_unavailable")

        if parsed.ambiguous:
            return SemanticProductResolution(
                matched=False,
                confidence=parsed.confidence,
                reason_code="ambiguous",
                ambiguous=True,
            )
        allowed_ids = {candidate.product_id for candidate in candidates}
        if (
            parsed.product_id not in allowed_ids
            or parsed.confidence < self._min_confidence
        ):
            return SemanticProductResolution(
                confidence=parsed.confidence,
                reason_code=(
                    "low_confidence"
                    if parsed.product_id in allowed_ids
                    else "no_match"
                ),
            )
        return SemanticProductResolution(
            matched=True,
            product_id=parsed.product_id,
            confidence=parsed.confidence,
            reason_code="semantic_match",
        )
