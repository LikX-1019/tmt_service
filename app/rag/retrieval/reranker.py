"""统一 RetrievalDocument 的 BGE rerank 阶段。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings, get_settings
from app.factories.reranker_factory import RerankerFactory
from app.qa.models import RetrievalDocument


class BGEReranker:
    def __init__(
        self, model: Any | None = None, *, settings: Settings | None = None
    ) -> None:
        self._model = model
        self._settings = settings or get_settings()

    async def rerank(
        self, query: str, documents: list[RetrievalDocument]
    ) -> list[RetrievalDocument]:
        if not documents:
            return []
        if self._model is None:
            self._model = await asyncio.to_thread(
                RerankerFactory.create, self._settings
            )
        scores = await asyncio.to_thread(
            self._model.score, query, [document.content for document in documents]
        )
        ranked = [
            document.copy(rerank_score=score)
            for document, score in zip(documents, scores, strict=True)
        ]
        ranked.sort(
            key=lambda item: (
                item.rerank_score
                if item.rerank_score is not None
                else float("-inf")
            ),
            reverse=True,
        )
        return ranked[: self._settings.rag_rerank_top_k]
