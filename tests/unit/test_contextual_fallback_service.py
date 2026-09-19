import pytest

from app.qa.models import RetrievalDocument
from app.services.contextual_fallback_service import (
    ContextualFallbackAnswer,
    ContextualFallbackService,
)
from app.services.product_service import ProductProfile


pytestmark = pytest.mark.asyncio


class CaptureLLM:
    def __init__(self, result: ContextualFallbackAnswer) -> None:
        self.result = result
        self.messages = []

    async def ainvoke(self, messages):
        self.messages = messages
        return self.result


async def test_contextual_fallback_sends_history_product_and_references() -> None:
    llm = CaptureLLM(
        ContextualFallbackAnswer(
            answer="可以装 80 度水，但不要超过耐温上限。",
            confidence=0.95,
        )
    )
    product = ProductProfile(
        id="TEST-BOTTLE-002",
        name="测试商品-运动水壶",
        summary="运动水壶。",
        specifications={"耐温": "-20℃ 至 90℃"},
    )
    references = [
        RetrievalDocument(
            chunk_id="qa-1",
            content="问题：能装热水吗？\n回答：可参考耐温范围。",
            rerank_score=0.5,
        )
    ]

    result = await ContextualFallbackService(llm).generate(
        "80度的水可以装吗",
        history=[
            {"customer": "介绍一下", "assistant": "这是水壶。"},
            {"customer": "80度的水可以装吗", "assistant": None},
        ],
        product=product,
        references=references,
    )

    prompt = llm.messages[0].content
    assert result.answer.startswith("可以装 80 度水")
    assert "80度的水可以装吗" in llm.messages[1].content
    assert "用户：介绍一下" in prompt
    assert "客服：这是水壶。" in prompt
    assert "- 耐温：-20℃ 至 90℃" in prompt
    assert "能装热水吗？" in prompt
    assert "qa-1" not in prompt


async def test_low_confidence_requires_human() -> None:
    llm = CaptureLLM(
        ContextualFallbackAnswer(answer="无法确认", confidence=0.3)
    )

    result = await ContextualFallbackService(llm).generate("发货时效是多少")

    assert result.needs_human is True
    assert result.reason_code == "low_confidence"
