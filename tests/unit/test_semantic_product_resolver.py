import pytest

from app.services.product_resolver import ConversationProductReference
from app.services.semantic_product_resolver import (
    SemanticProductResolver,
    _LLMSemanticProductResolution,
)

CANDIDATES = [
    ConversationProductReference("TEST-MAT-003", "测试商品-瑜伽垫"),
    ConversationProductReference("TEST-BOTTLE-002", "测试商品-运动水壶"),
]


class StubLLM:
    def __init__(self, result, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.messages = []

    async def ainvoke(self, messages):
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_semantic_resolver_maps_colloquial_alias_within_candidates() -> None:
    llm = StubLLM(
        _LLMSemanticProductResolution(
            product_id="TEST-BOTTLE-002", confidence=0.92, ambiguous=False
        )
    )

    result = await SemanticProductResolver(llm).resolve(
        "那这个水杯的容量呢",
        candidates=CANDIDATES,
        current_product_id="TEST-MAT-003",
    )

    assert result.matched is True
    assert result.product_id == "TEST-BOTTLE-002"
    assert result.reason_code == "semantic_match"
    assert "product_id=TEST-BOTTLE-002" in llm.messages[1].content
    assert "水杯" in llm.messages[1].content


@pytest.mark.asyncio
async def test_semantic_resolver_marks_multiple_matches_ambiguous() -> None:
    result = await SemanticProductResolver(
        StubLLM(_LLMSemanticProductResolution(confidence=0.9, ambiguous=True))
    ).resolve("那个垫子", candidates=CANDIDATES, current_product_id=None)

    assert result.matched is False
    assert result.ambiguous is True
    assert result.reason_code == "ambiguous"


@pytest.mark.asyncio
async def test_semantic_resolver_rejects_unknown_product_id() -> None:
    result = await SemanticProductResolver(
        StubLLM(
            _LLMSemanticProductResolution(
                product_id="unknown-product", confidence=0.99
            )
        )
    ).resolve("那个水杯", candidates=CANDIDATES)

    assert result.matched is False
    assert result.product_id is None
    assert result.reason_code == "no_match"


@pytest.mark.asyncio
async def test_semantic_resolver_survives_llm_failure() -> None:
    result = await SemanticProductResolver(
        StubLLM(None, error=RuntimeError("llm unavailable"))
    ).resolve("那这个水杯呢", candidates=CANDIDATES)

    assert result.matched is False
    assert result.reason_code == "resolver_unavailable"
