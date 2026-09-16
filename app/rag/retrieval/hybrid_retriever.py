"""并行执行 Dense/BM25 并用 RRF 融合。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.qa.models import RetrievalDocument
from app.rag.retrieval.fusion import reciprocal_rank_fusion


class HybridRetriever:
    def __init__(
        self,
        dense_retriever: Any,
        bm25_retriever: Any,
        *,
        rrf_k: int = 60,
        top_k: int = 20,
    ) -> None:
        self._dense = dense_retriever
        self._bm25 = bm25_retriever
        self._rrf_k = rrf_k
        self._top_k = top_k
        self.last_counts = {"dense": 0, "bm25": 0, "fusion": 0}

    async def retrieve(self, query: str) -> list[RetrievalDocument]:
        dense, bm25 = await asyncio.gather(
            self._dense.retrieve(query), self._bm25.retrieve(query)
        )
        fused = reciprocal_rank_fusion(
            [dense, bm25], rrf_k=self._rrf_k, top_k=self._top_k
        )
        self.last_counts = {
            "dense": len(dense),
            "bm25": len(bm25),
            "fusion": len(fused),
        }
        return fused
