"""通过 EmbeddingFactory 与 VectorStoreFactory 执行稠密检索。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings, get_settings
from app.factories.embedding_factory import EmbeddingFactory
from app.factories.vectorstore_factory import VectorStoreFactory
from app.qa.models import RetrievalDocument


class DenseRetriever:
    def __init__(
        self,
        embedder: Any | None = None,
        vector_store: Any | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(self._settings.embedding_max_concurrency)

    async def retrieve(self, query: str) -> list[RetrievalDocument]:
        async with self._semaphore:
            if self._embedder is None:
                self._embedder = await asyncio.to_thread(
                    EmbeddingFactory.create, self._settings
                )
            vector = await asyncio.to_thread(self._embedder.embed_query, query)
        if self._vector_store is None:
            self._vector_store = VectorStoreFactory.create(self._settings)
        return await asyncio.to_thread(
            self._vector_store.search,
            vector,
            self._settings.rag_dense_top_k,
        )
