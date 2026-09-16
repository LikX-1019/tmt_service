"""QA 模块内部统一数据对象。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal


@dataclass(slots=True)
class FAQItem:
    id: str
    question: str
    answer: str
    aliases: list[str] = field(default_factory=list)
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievalDocument:
    chunk_id: str
    content: str
    title: str | None = None
    source: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    dense_score: float | None = None
    bm25_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None

    def copy(self, **changes: object) -> "RetrievalDocument":
        changes.setdefault("metadata", dict(self.metadata))
        return replace(self, **changes)


@dataclass(slots=True)
class QASource:
    chunk_id: str
    title: str | None = None
    source: str | None = None
    score: float | None = None


@dataclass(slots=True)
class QAResult:
    answer: str
    route: Literal["faq", "rag", "fallback"]
    sources: list[QASource] = field(default_factory=list)
    confidence: float | None = None
    match_score: float | None = None
    auto_reply_confidence: float | None = None
    decision_factors: dict[str, object] = field(default_factory=dict, repr=False)
    trace_documents: list[RetrievalDocument] = field(default_factory=list, repr=False)
    retrieval_counts: dict[str, int] = field(default_factory=dict, repr=False)
