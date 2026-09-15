"""Milvus 向量存储适配器与工厂。"""

from __future__ import annotations

import json
from threading import Lock

from app.core.config import Settings, get_settings
from app.qa.models import RetrievalDocument


class MilvusVectorStore:
    def __init__(self, uri: str, collection: str) -> None:
        from pymilvus import MilvusClient

        self._client = MilvusClient(uri=uri)
        self.collection = collection

    def ensure_collection(self, dimension: int) -> None:
        if self._client.has_collection(self.collection):
            return
        from pymilvus import DataType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(
            field_name="chunk_id",
            datatype=DataType.VARCHAR,
            is_primary=True,
            max_length=512,
        )
        schema.add_field(
            field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension
        )
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=2048)
        schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=2048)
        schema.add_field(field_name="metadata", datatype=DataType.JSON)
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector", index_type="AUTOINDEX", metric_type="COSINE"
        )
        self._client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=index_params,
        )

    def upsert(
        self, documents: list[RetrievalDocument], vectors: list[list[float]]
    ) -> int:
        if len(documents) != len(vectors):
            raise ValueError("documents 与 vectors 数量不一致")
        if not documents:
            return 0
        self.ensure_collection(len(vectors[0]))
        data = [
            {
                "chunk_id": document.chunk_id,
                "vector": vector,
                "content": document.content,
                "title": document.title or "",
                "source": document.source or "",
                "metadata": document.metadata,
            }
            for document, vector in zip(documents, vectors, strict=True)
        ]
        result = self._client.upsert(collection_name=self.collection, data=data)
        return int(result.get("upsert_count", len(data)))

    def search(self, vector: list[float], top_k: int) -> list[RetrievalDocument]:
        if not self._client.has_collection(self.collection):
            return []
        results = self._client.search(
            collection_name=self.collection,
            data=[vector],
            limit=top_k,
            anns_field="vector",
            output_fields=["chunk_id", "content", "title", "source", "metadata"],
            search_params={"metric_type": "COSINE"},
        )
        documents: list[RetrievalDocument] = []
        for hit in results[0] if results else []:
            entity = hit.get("entity", {})
            metadata = entity.get("metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            documents.append(
                RetrievalDocument(
                    chunk_id=str(entity.get("chunk_id") or hit.get("id")),
                    content=str(entity.get("content") or ""),
                    title=entity.get("title") or None,
                    source=entity.get("source") or None,
                    metadata=dict(metadata),
                    dense_score=float(hit.get("distance", 0.0)),
                )
            )
        return documents


class VectorStoreFactory:
    _instances: dict[tuple[str, str], MilvusVectorStore] = {}
    _lock = Lock()

    @classmethod
    def create(cls, settings: Settings | None = None) -> MilvusVectorStore:
        settings = settings or get_settings()
        provider = settings.vector_store.strip().lower()
        if provider != "milvus":
            raise ValueError(f"不支持的 VECTOR_STORE：{provider}")
        key = (settings.milvus_uri, settings.milvus_collection)
        if key not in cls._instances:
            with cls._lock:
                if key not in cls._instances:
                    cls._instances[key] = MilvusVectorStore(*key)
        return cls._instances[key]

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._instances.clear()
