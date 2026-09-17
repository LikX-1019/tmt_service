from unittest.mock import AsyncMock

import pytest

from app.qa.evidence_checker import EvidenceChecker
from app.qa.faq_matcher import FAQMatcher
from app.qa.models import FAQItem, RetrievalDocument
from app.qa.service import QAService


def make_service(
    *,
    faq_items: list[FAQItem] | None = None,
    retrieved: list[RetrievalDocument] | None = None,
    reranked: list[RetrievalDocument] | None = None,
) -> tuple[QAService, AsyncMock, AsyncMock, AsyncMock]:
    retriever = AsyncMock()
    retriever.retrieve.return_value = retrieved or []
    reranker = AsyncMock()
    reranker.rerank.return_value = reranked or []
    generator = AsyncMock()
    generator.generate.return_value = "根据资料，可以整鞋水洗。"
    service = QAService(
        FAQMatcher(faq_items or []),
        retriever,
        reranker,
        EvidenceChecker(0.5),
        generator,
    )
    return service, retriever, reranker, generator


@pytest.mark.asyncio
async def test_faq_hit_skips_retrieval_and_llm() -> None:
    item = FAQItem(id="FAQ1", question="支持七天无理由吗", answer="支持。")
    service, retriever, reranker, generator = make_service(faq_items=[item])

    result = await service.answer("支持七天无理由吗？")

    assert result.route == "faq"
    assert result.confidence == 1.0
    retriever.retrieve.assert_not_awaited()
    reranker.rerank.assert_not_awaited()
    generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_path_generates_from_sufficient_evidence() -> None:
    retrieved = [RetrievalDocument(chunk_id="c1", content="说明")]
    reranked = [
        RetrievalDocument(
            chunk_id="c1",
            content="说明",
            title="清洗说明",
            source="manual.md",
            rerank_score=0.9,
        )
    ]
    service, retriever, reranker, generator = make_service(
        retrieved=retrieved, reranked=reranked
    )

    result = await service.answer("这款可以整鞋水洗吗")

    assert result.route == "rag"
    assert result.sources[0].chunk_id == "c1"
    retriever.retrieve.assert_awaited_once()
    reranker.rerank.assert_awaited_once()
    generator.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_skips_answer_generator() -> None:
    retrieved = [RetrievalDocument(chunk_id="c1", content="无关内容")]
    reranked = [
        RetrievalDocument(chunk_id="c1", content="无关内容", rerank_score=0.1)
    ]
    service, _, _, generator = make_service(retrieved=retrieved, reranked=reranked)

    result = await service.answer("你们公司的老板是谁")

    assert result.route == "fallback"
    assert result.sources == []
    generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_uses_reviewed_standard_answer_when_generation_fails() -> None:
    retrieved = [RetrievalDocument(chunk_id="QA-1", content="标准知识")]
    reranked = [
        RetrievalDocument(
            chunk_id="QA-1",
            content="标准知识",
            metadata={"answer": "这是已审核的标准回答"},
            rerank_score=0.9,
        )
    ]
    service, _, _, generator = make_service(
        retrieved=retrieved, reranked=reranked
    )
    generator.generate.side_effect = TimeoutError("provider unavailable")

    result = await service.answer("相似问法")

    assert result.route == "rag"
    assert result.answer == "这是已审核的标准回答"
    assert result.decision_factors["generation_fallback"] is True
