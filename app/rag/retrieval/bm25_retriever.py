"""初始化一次、请求期间只查询的中文 BM25 检索器。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable

from app.qa.models import RetrievalDocument


_ASCII_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    import jieba

    normalized = text.lower().strip()
    tokens = [token.strip() for token in jieba.lcut_for_search(normalized) if token.strip()]
    tokens.extend(_ASCII_TOKEN.findall(normalized))
    return tokens


class BM25Retriever:
    def __init__(self, documents: Iterable[RetrievalDocument], top_k: int = 20) -> None:
        from rank_bm25 import BM25Okapi

        self._documents = [document.copy() for document in documents]
        self._top_k = top_k
        corpus = [_tokenize(document.content) for document in self._documents]
        self._index = BM25Okapi(corpus) if corpus else None

    def _retrieve_sync(self, query: str) -> list[RetrievalDocument]:
        if self._index is None:
            return []
        scores = self._index.get_scores(_tokenize(query))
        ranked = sorted(
            enumerate(scores), key=lambda item: float(item[1]), reverse=True
        )[: self._top_k]
        return [
            self._documents[index].copy(bm25_score=float(score))
            for index, score in ranked
            if float(score) > 0
        ]

    async def retrieve(self, query: str) -> list[RetrievalDocument]:
        return await asyncio.to_thread(self._retrieve_sync, query)
