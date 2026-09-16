"""Milvus 向量存储适配器与工厂。"""

from __future__ import annotations

import json
from threading import Lock

from app.core.config import Settings, get_settings
from app.qa.models import RetrievalDocument


class MilvusVectorStore:
    def __init__(
        self,
        uri: str,
        collection: str,
        schema_version: int,
        embedding_model: str,
        embedding_version: str,
    ) -> None:
        from pymilvus import MilvusClient

        self._client = MilvusClient(uri=uri)
        self.collection = collection
        self._schema_version = schema_version
        self._embedding_model = embedding_model
        self._embedding_version = embedding_version

    def ensure_collection(self, dimension: int) -> None:
        if self._client.has_collection(self.collection):
            description = self._client.describe_collection(self.collection)
            fields = {field["name"]: field for field in description.get("fields", [])}
            vector = fields.get("vector", {})
            actual_dimension = int(vector.get("params", {}).get("dim", 0))
            required = {"document_id", "document_version", "embedding_model", "schema_version", "active"}
            if actual_dimension != dimension or not required.issubset(fields):
                raise RuntimeError(
                    f"Milvus collection {self.collection!r} schema incompatible; "
                    "use a new versioned MILVUS_COLLECTION"
                )
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
        schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="document_version", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="embedding_model", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="embedding_version", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="schema_version", datatype=DataType.INT64)
        schema.add_field(field_name="active", datatype=DataType.BOOL)
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
                "document_id": str(document.metadata.get("document_id") or document.chunk_id),
                "document_version": str(document.metadata.get("document_version") or "v1"),
                "embedding_model": self._embedding_model,
                "embedding_version": self._embedding_version,
                "schema_version": self._schema_version,
                "active": bool(document.metadata.get("active", False)),
            }
            for document, vector in zip(documents, vectors, strict=True)
        ]
        result = self._client.upsert(collection_name=self.collection, data=data)
        return int(result.get("upsert_count", len(data)))

    def activate_versions(self, documents: list[RetrievalDocument]) -> None:
        """验证新版本 chunk 完整后激活，并删除同文档的旧版本。"""
        versions: dict[tuple[str, str], int] = {}
        for document in documents:
            key = (
                str(document.metadata.get("document_id") or document.chunk_id),
                str(document.metadata.get("document_version") or "v1"),
            )
            versions[key] = versions.get(key, 0) + 1
        output_fields = [
            "chunk_id", "vector", "content", "title", "source", "metadata",
            "document_id", "document_version", "embedding_model",
            "embedding_version", "schema_version", "active",
        ]
        for (document_id, version), expected in versions.items():
            document_literal = json.dumps(document_id, ensure_ascii=False)
            version_literal = json.dumps(version, ensure_ascii=False)
            rows = self._client.query(
                collection_name=self.collection,
                filter=f"document_id == {document_literal} and document_version == {version_literal}",
                output_fields=output_fields,
                limit=expected + 1,
            )
            if len(rows) != expected:
                raise RuntimeError(f"文档 {document_id} 新版本 chunk 校验失败")
            for row in rows:
                row["active"] = True
                metadata = dict(row.get("metadata") or {})
                metadata["active"] = True
                row["metadata"] = metadata
            self._client.upsert(collection_name=self.collection, data=rows)
            self._client.delete(
                collection_name=self.collection,
                filter=f"document_id == {document_literal} and document_version != {version_literal}",
            )

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
            filter="active == true",
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
    _instances: dict[tuple[str, str, int, str, str], MilvusVectorStore] = {}
    _lock = Lock()

    @classmethod
    def create(cls, settings: Settings | None = None) -> MilvusVectorStore:
        settings = settings or get_settings()
        provider = settings.vector_store.strip().lower()
        if provider != "milvus":
            raise ValueError(f"不支持的 VECTOR_STORE：{provider}")
        model = settings.embedding_model or str(settings.embedding_model_path)
        key = (
            settings.milvus_uri,
            settings.milvus_collection,
            settings.milvus_schema_version,
            model,
            settings.embedding_version,
        )
        if key not in cls._instances:
            with cls._lock:
                if key not in cls._instances:
                    cls._instances[key] = MilvusVectorStore(*key)
        return cls._instances[key]

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._instances.clear()
