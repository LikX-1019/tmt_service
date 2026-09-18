"""客服消息的受控路由：售后强规则、商品咨询与通用知识。"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.factories.llm_factory import LLMFactory


HANDOFF_PATTERN = re.compile(
    r"退款|退货|换货|取消订单|取消.*订单|补发|赔付|赔偿|投诉|差评|平台介入"
)
PRODUCT_HINT_PATTERN = re.compile(
    r"商品|这款|这个|材质|成分|规格|尺寸|尺码|颜色|款式|使用|怎么用|适用|功效|保养|清洗|重量|容量"
)


class RouteClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["product", "rag"]
    confidence: float = Field(ge=0, le=1)


class CustomerServiceRouter:
    """模型分类只决定 FAQ 未命中后的商品/通用路径，失败时保守地用规则兜底。"""

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    @staticmethod
    def requires_handoff(query: str) -> bool:
        return bool(HANDOFF_PATTERN.search(query))

    async def classify(self, query: str, *, has_product_context: bool) -> RouteClassification:
        if PRODUCT_HINT_PATTERN.search(query):
            return RouteClassification(route="product", confidence=1.0)
        try:
            llm = self._llm or await asyncio.to_thread(
                LLMFactory.get_structured_llm, "routing", RouteClassification, 0.0
            )
            result = await llm.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "你是电商客服路由器。仅返回 product 或 rag。"
                            "product 表示问题需要商品规格、用途、注意事项等商品事实；"
                            "rag 表示通用店铺知识问题。售后、订单争议不应到这里。"
                        )
                    ),
                    HumanMessage(
                        content=(
                            f"当前会话是否有商品卡片：{has_product_context}\n用户问题：{query}"
                        )
                    ),
                ]
            )
            return RouteClassification.model_validate(result)
        except Exception:
            # 模型异常不能阻断基础通用问题；显式商品线索已在上方被规则捕获。
            return RouteClassification(route="rag", confidence=0.0)
