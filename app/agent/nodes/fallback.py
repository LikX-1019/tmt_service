"""Unified Chat Contextual Fallback 节点。"""

from __future__ import annotations

import logging

from app.agent.dependencies import AgentCapabilities
from app.agent.state import (
    AgentReplyState,
    AgentState,
    HumanState,
    RetrievalEvidence,
    RetrievalState,
    StatePatch,
)
from app.core.exceptions import LLMInvocationError
from app.services.product_service import ProductProfile

logger = logging.getLogger(__name__)


def _profile_from_state(state: AgentState) -> ProductProfile | None:
    """优先使用完整当前 Turn profile；缺省时用 snapshot 重建最小资料。"""
    if state.turn is None or state.turn.product.context is None:
        return None
    if state.turn.product.profile:
        return ProductProfile.model_validate(state.turn.product.profile)
    context = state.turn.product.context
    return ProductProfile(
        id=context.product_id,
        name=context.name,
        summary=context.summary or "",
        category_name=context.category,
        selling_points=context.selling_points,
        specifications=context.specifications,
        usage="\n".join(context.usage),
        suitable_for="\n".join(context.suitable_for),
        warnings="\n".join(context.warnings),
        after_sales_limits="\n".join(context.after_sales_limits),
    )


class FallbackNode:
    """尽力检索 RAG context，然后调用 ContextualFallbackService。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        if state.session is None or state.turn is None:
            raise ValueError("FallbackNode 前必须完成 hydration")

        references = []
        if self._capabilities.qa_provider is not None:
            try:
                qa_service = await self._capabilities.qa_provider()
                references = await qa_service.retrieve_context_candidates(
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

        history = [
            {"customer": item.customer, "assistant": item.assistant}
            for item in state.session.short_term_memory.recent_turns
        ]
        try:
            result = await self._capabilities.fallbacks.generate(
                state.turn.original_query,
                history=history,
                product=_profile_from_state(state),
                references=references,
            )
        except LLMInvocationError:
            raise
        except Exception as exc:
            logger.exception(
                "contextual_fallback_failed",
                extra={
                    "event": "contextual_fallback_failed",
                    "fallback_reason": "llm_unavailable",
                },
            )
            raise LLMInvocationError() from exc

        evidence = [
            RetrievalEvidence(
                document_id=str(item.metadata.get("document_id") or item.chunk_id),
                chunk_id=item.chunk_id,
                content=item.content,
                source=item.source,
                product_id=(
                    str(item.metadata.get("product_id"))
                    if item.metadata.get("product_id") is not None
                    else None
                ),
                dense_score=item.dense_score,
                bm25_score=item.bm25_score,
                fusion_score=item.fusion_score,
                rerank_score=item.rerank_score,
            )
            for item in references
        ]
        route = "human" if result.needs_human else "llm_fallback"
        patch = StatePatch(
            retrieval=RetrievalState(
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
            ),
            evidence=evidence,
            route="human" if result.needs_human else "llm_fallback",
            fallback_reason=result.reason_code,
            final_answer=result.answer,
            reply=AgentReplyState(
                source="llm_fallback",
                route=route,
                answer=result.answer,
                product_id=(
                    state.turn.product.context.product_id
                    if state.turn.product.context
                    else None
                ),
                product_name=(
                    state.turn.product.context.name
                    if state.turn.product.context
                    else None
                ),
                product_resolution=(
                    "conversation"
                    if state.turn.product.context is not None
                    else "none"
                ),
                reason_code=result.reason_code,
                confidence=result.confidence,
            ),
            next_node="response",
        )
        if result.needs_human:
            patch.human = HumanState(
                required=True,
                status="requested",
                reason_code=result.reason_code or "fallback_needs_human",
            )
        return patch


fallback_node = FallbackNode

__all__ = ["FallbackNode", "fallback_node"]
