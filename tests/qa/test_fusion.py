import pytest

from app.qa.models import RetrievalDocument
from app.rag.retrieval.fusion import reciprocal_rank_fusion


def document(chunk_id: str, **scores: float) -> RetrievalDocument:
    return RetrievalDocument(chunk_id=chunk_id, content=chunk_id, **scores)


def test_rrf_deduplicates_and_combines_ranks() -> None:
    fused = reciprocal_rank_fusion(
        [
            [document("shared", dense_score=0.9), document("dense-only")],
            [document("bm25-only"), document("shared", bm25_score=8.0)],
        ],
        rrf_k=60,
        top_k=3,
    )

    assert [item.chunk_id for item in fused] == ["shared", "bm25-only", "dense-only"]
    assert fused[0].fusion_score == pytest.approx(1 / 61 + 1 / 62)
    assert fused[0].dense_score == 0.9
    assert fused[0].bm25_score == 8.0
