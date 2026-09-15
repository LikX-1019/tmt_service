"""Embedding 模型工厂；首次使用时才加载本地模型。"""

from __future__ import annotations

from threading import Lock
from typing import Any

from app.core.config import Settings, get_settings


class SentenceTransformerEmbeddings:
    def __init__(self, model: Any) -> None:
        self._model = model

    def embed_query(self, query: str) -> list[float]:
        vector = self._model.encode(
            query, normalize_embeddings=True, show_progress_bar=False
        )
        return vector.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=16,
        )
        return vectors.tolist()


class EmbeddingFactory:
    _instances: dict[str, SentenceTransformerEmbeddings] = {}
    _lock = Lock()

    @classmethod
    def create(
        cls, settings: Settings | None = None
    ) -> SentenceTransformerEmbeddings:
        settings = settings or get_settings()
        provider = settings.embedding_provider.strip().lower()
        if provider not in {"sentence_transformers", "huggingface"}:
            raise ValueError(f"不支持的 EMBEDDING_PROVIDER：{provider}")
        model_name = settings.embedding_model or str(settings.embedding_model_path)
        if model_name not in cls._instances:
            with cls._lock:
                if model_name not in cls._instances:
                    from sentence_transformers import SentenceTransformer

                    cls._instances[model_name] = SentenceTransformerEmbeddings(
                        SentenceTransformer(model_name)
                    )
        return cls._instances[model_name]

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._instances.clear()
