"""RAG context 节点：只检索证据，不生成最终回答。"""

from __future__ import annotations

import logging

from app.agent.constants import FALLBACK_NODE
from app.agent.dependencies import AgentCapabilities
from app.agent.state import (
    AgentState,
    RetrievalEvidence,
    RetrievalState,
    StatePatch,
)

logger = logging.getLogger(__name__)


class RAGContextNode:
    """Best-effort 检索 QA context，并将结果写入强类型 RetrievalState。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        if state.session is None or state.turn is None:
            raise ValueError("RAGContextNode 前必须完成 hydration")
        if self._capabilities.qa_provider is None:
            retrieval = RetrievalState(status="unavailable")
            return StatePatch(
                retrieval=retrieval,
                route="fallback",
                fallback_reason="knowledge_inactive",
                next_node=FALLBACK_NODE,
            )

        try:
            qa_service = await self._capabilities.qa_provider()
            documents = await qa_service.retrieve_context_candidates(
                state.turn.original_query
            )
        except Exception:
            logger.warning(
                "qa_fallback_context_retrieval_failed",
                exc_info=True,
                extra={
                    "event": "qa_fallback_context_retrieval_failed",
                    "fallback_reason": "rag_context_unavailable",
                },
            )
            retrieval = RetrievalState(status="unavailable")
            return StatePatch(
                retrieval=retrieval,
                route="fallback",
                fallback_reason="rag_context_unavailable",
                next_node=FALLBACK_NODE,
            )

        evidence = [
            RetrievalEvidence(
                document_id=str(
                    document.metadata.get("document_id") or document.chunk_id
                ),
                chunk_id=document.chunk_id,
                title=document.title,
                content=document.content,
                source=document.source,
                product_id=(
                    str(document.metadata.get("product_id"))
                    if document.metadata.get("product_id") is not None
                    else None
                ),
                dense_score=document.dense_score,
                bm25_score=document.bm25_score,
                fusion_score=document.fusion_score,
                rerank_score=document.rerank_score,
            )
            for document in documents
        ]
        retrieval = RetrievalState(
            status="available" if evidence else "empty",
            candidates=evidence,
            evidence_sufficient=bool(evidence),
            top_score=max(
                (
                    item.rerank_score
                    for item in evidence
                    if item.rerank_score is not None
                ),
                default=None,
            ),
        )
        return StatePatch(
            retrieval=retrieval,
            evidence=evidence,
            route="fallback",
            next_node=FALLBACK_NODE,
        )


rag_context_node = RAGContextNode

__all__ = ["RAGContextNode", "rag_context_node"]
