from __future__ import annotations

from typing import Any

from app.factories.vectorstore_factory import MilvusVectorStore
from app.qa.models import RetrievalDocument


class RecordingMilvusClient:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []
        self.upserts: list[list[dict[str, Any]]] = []
        self.deletes: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.queries.append(kwargs)
        document_id = kwargs["filter"].split('"')[1]
        return [
            {
                "chunk_id": document_id,
                "vector": [0.1],
                "content": "content",
                "title": "title",
                "source": "cs_qa",
                "metadata": {},
                "document_id": document_id,
                "document_version": "v1",
                "embedding_model": "test",
                "embedding_version": "v1",
                "schema_version": 1,
                "active": False,
            }
        ]

    def upsert(self, **kwargs: Any) -> dict[str, int]:
        self.upserts.append(kwargs["data"])
        return {"upsert_count": len(kwargs["data"])}

    def delete(self, **kwargs: Any) -> None:
        self.deletes.append(kwargs)


def make_store(client: RecordingMilvusClient) -> MilvusVectorStore:
    store = object.__new__(MilvusVectorStore)
    store._client = client
    store.collection = "customer_service_knowledge_test"
    store._schema_version = 1
    store._embedding_model = "test"
    store._embedding_version = "v1"
    return store


def test_activation_reads_new_chunks_with_strong_consistency() -> None:
    client = RecordingMilvusClient()
    store = make_store(client)
    documents = [
        RetrievalDocument(
            chunk_id="QA-1",
            content="content",
            metadata={"document_id": "QA-1", "document_version": "v1"},
        )
    ]

    store.activate_versions(documents)

    assert len(client.queries) == 1
    assert client.queries[0]["consistency_level"] == "Strong"
    assert client.upserts[0][0]["active"] is True
    assert client.deletes[0]["filter"] == 'document_id == "QA-1" and document_version != "v1"'
