"""优先保留 Markdown、段落和句子边界的知识切块器。"""

from __future__ import annotations

import hashlib
import re

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
        blocks = [
            part.strip()
            for part in re.split(r"\n{2,}|(?<=[。！？!?])", document.content)
            if part.strip()
        ]
        contents: list[str] = []
        current = ""
        for block in blocks:
            if len(block) > self._chunk_size:
                if current:
                    contents.append(current)
                    current = ""
                contents.extend(
                    block[start : start + self._chunk_size]
                    for start in range(0, len(block), self._step)
                )
            elif not current or len(current) + 2 + len(block) <= self._chunk_size:
                current = f"{current}\n\n{block}".strip()
            else:
                contents.append(current)
                overlap = current[-(self._chunk_size - self._step) :].strip()
                current = f"{overlap}\n\n{block}".strip()
        if current:
            contents.append(current)

        chunks: list[RetrievalDocument] = []
        document_id = str(document.metadata.get("document_id") or document.chunk_id)
        document_version = str(document.metadata.get("document_version") or "v1")
        for index, content in enumerate(contents):
            content = content.strip()
            if not content:
                continue
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            digest = content_hash[:12]
            chunks.append(
                document.copy(
                    chunk_id=f"{document.chunk_id}-{digest}",
                    content=content,
                    metadata={
                        **document.metadata,
                        "document_id": document_id,
                        "document_version": document_version,
                        "content_hash": content_hash,
                        "chunk_index": index,
                        "active": False,
                    },
                )
            )
        return chunks
