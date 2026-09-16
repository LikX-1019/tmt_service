"""不调用 LLM 的确定性证据门槛。"""

from __future__ import annotations

from app.qa.models import RetrievalDocument


class EvidenceChecker:
    def __init__(self, score_threshold: float) -> None:
        self._threshold = score_threshold

    def is_sufficient(self, documents: list[RetrievalDocument]) -> bool:
        if not documents or documents[0].rerank_score is None:
            return False
        return documents[0].rerank_score >= self._threshold
