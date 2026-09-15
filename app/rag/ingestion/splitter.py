"""可预测的字符级切块器。"""

from __future__ import annotations

import hashlib

from app.qa.models import RetrievalDocument


class KnowledgeSplitter:
    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 120) -> None:
        if chunk_size < 1 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_size 必须为正，且 chunk_overlap 小于 chunk_size")
        self._chunk_size = chunk_size
        self._step = chunk_size - chunk_overlap

    def split(self, document: RetrievalDocument) -> list[RetrievalDocument]:
        if not document.content:
            return []
        chunks: list[RetrievalDocument] = []
        for index, start in enumerate(range(0, len(document.content), self._step)):
            content = document.content[start : start + self._chunk_size].strip()
            if not content:
                continue
            digest = hashlib.sha1(
                f"{document.chunk_id}:{index}:{content}".encode("utf-8")
            ).hexdigest()[:12]
            chunks.append(
                document.copy(
                    chunk_id=f"{document.chunk_id}-{digest}",
                    content=content,
                    metadata={**document.metadata, "chunk_index": index},
                )
            )
            if start + self._chunk_size >= len(document.content):
                break
        return chunks
