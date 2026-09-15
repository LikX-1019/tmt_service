"""批量生成向量并写入统一 VectorStore。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.qa.models import RetrievalDocument


class KnowledgeIndexer:
    def __init__(self, embedder: Any, vector_store: Any, batch_size: int = 16) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._batch_size = batch_size

    async def index(self, documents: list[RetrievalDocument]) -> tuple[int, int]:
        succeeded = 0
        failed = 0
        for start in range(0, len(documents), self._batch_size):
            batch = documents[start : start + self._batch_size]
            try:
                vectors = await asyncio.to_thread(
                    self._embedder.embed_documents,
                    [document.content for document in batch],
                )
                count = await asyncio.to_thread(
                    self._vector_store.upsert, batch, vectors
                )
            except Exception:
                failed += len(batch)
            else:
                succeeded += count
                failed += max(0, len(batch) - count)
        return succeeded, failed
