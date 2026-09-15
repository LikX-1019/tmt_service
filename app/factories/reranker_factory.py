"""BGE CrossEncoder reranker 工厂。"""

from __future__ import annotations

from threading import Lock
from typing import Any

from app.core.config import Settings, get_settings


class CrossEncoderReranker:
    def __init__(self, model: Any) -> None:
        self._model = model

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        scores = self._model.predict(
            [(query, content) for content in documents],
            show_progress_bar=False,
        )
        return [float(score) for score in scores]


class RerankerFactory:
    _instances: dict[str, CrossEncoderReranker] = {}
    _lock = Lock()

    @classmethod
    def create(cls, settings: Settings | None = None) -> CrossEncoderReranker:
        settings = settings or get_settings()
        provider = settings.reranker_provider.strip().lower()
        if provider not in {"sentence_transformers", "huggingface"}:
            raise ValueError(f"不支持的 RERANKER_PROVIDER：{provider}")
        model_name = settings.reranker_model or str(settings.reranker_model_path)
        if model_name not in cls._instances:
            with cls._lock:
                if model_name not in cls._instances:
                    from sentence_transformers import CrossEncoder

                    cls._instances[model_name] = CrossEncoderReranker(
                        CrossEncoder(model_name)
                    )
        return cls._instances[model_name]

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._instances.clear()
