"""基于排名而非异构原始分值的 RRF 融合。"""

from __future__ import annotations

from collections.abc import Sequence

from app.qa.models import RetrievalDocument


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievalDocument]],
    *,
    rrf_k: int = 60,
    top_k: int = 20,
) -> list[RetrievalDocument]:
    if rrf_k < 1 or top_k < 1:
        raise ValueError("rrf_k 和 top_k 必须大于 0")

    fused: dict[str, RetrievalDocument] = {}
    for documents in ranked_lists:
        for rank, document in enumerate(documents, start=1):
            existing = fused.get(document.chunk_id)
            if existing is None:
                existing = document.copy(fusion_score=0.0)
                fused[document.chunk_id] = existing
            existing.fusion_score = (existing.fusion_score or 0.0) + 1.0 / (
                rrf_k + rank
            )
            if document.dense_score is not None:
                existing.dense_score = document.dense_score
            if document.bm25_score is not None:
                existing.bm25_score = document.bm25_score

    return sorted(
        fused.values(),
        key=lambda item: item.fusion_score or 0.0,
        reverse=True,
    )[:top_k]
