from unittest.mock import Mock

import pytest

from app.qa.models import RetrievalDocument
from app.rag.ingestion.indexer import KnowledgeIndexer


class Embedder:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


@pytest.mark.asyncio
async def test_indexer_activates_only_after_all_batches_succeed() -> None:
    store = Mock()
    store.upsert.side_effect = lambda documents, vectors: len(documents)
    documents = [RetrievalDocument(chunk_id=str(index), content="fact") for index in range(3)]

    result = await KnowledgeIndexer(Embedder(), store, batch_size=2).index(documents)

    assert result == (3, 0)
    store.activate_versions.assert_called_once_with(documents)


@pytest.mark.asyncio
async def test_indexer_does_not_activate_partial_write() -> None:
    store = Mock()
    store.upsert.side_effect = [2, RuntimeError("write failed")]
    documents = [RetrievalDocument(chunk_id=str(index), content="fact") for index in range(3)]

    result = await KnowledgeIndexer(Embedder(), store, batch_size=2).index(documents)

    assert result == (2, 1)
    store.activate_versions.assert_not_called()
