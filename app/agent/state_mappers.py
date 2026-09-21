"""State Contract 的纯映射函数，供 AgentGraph Node 使用。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agent.state import (
    AgentReplyState,
    AgentSourceState,
    HumanState,
    RetrievalEvidence,
    RetrievalState,
    StatePatch,
)
from app.qa.models import QAResult


def _utcnow() -> datetime:
    """生成 State Contract 使用的 timezone-aware UTC 时间。"""
    return datetime.now(timezone.utc)


def rule_result_to_patch(
    result: Any,
) -> StatePatch:
    """把确定性规则结果映射为 StatePatch，不修改任何 State。"""
    terminal = result.terminal_decision
    next_node = "response" if terminal is not None else "faq_exact"
    primary = terminal or (result.decisions[0] if result.decisions else None)
    patch = StatePatch(
        context={
            "guard_terminal": terminal is not None,
            "guard_requires_product": result.requires_product,
            "guard_product_rule_name": next(
                (
                    decision.rule_name
                    for decision in result.decisions
                    if decision.requires_product and decision.rule_name
                ),
                None,
            ),
            "guard_rule_names": [
                decision.rule_name
                for decision in result.decisions
                if decision.rule_name
            ],
        },
        intent=primary.reason_code or primary.rule_name if primary else None,
        next_node=next_node,
    )
    if terminal is not None:
        patch.route = terminal.route
        patch.intent = terminal.reason_code or terminal.rule_name
        patch.final_answer = terminal.fixed_reply
        if terminal.fixed_reply is not None:
            patch.reply = AgentReplyState(
                source="rule",
                route=terminal.route,
                answer=terminal.fixed_reply,
                rule_name=terminal.rule_name,
                reason_code=terminal.reason_code,
                confidence=terminal.confidence,
            )
    if result.requires_human:
        reason_code = primary.reason_code if primary else "human_required"
        patch.human = HumanState(
            required=True,
            status="requested",
            reason_code=reason_code,
            requested_at=_utcnow(),
        )
        patch.risk_level = "high"
        if reason_code:
            patch.risk_reasons = [reason_code]
    if terminal is not None and terminal.route == "fallback" and terminal.reason_code:
        patch.fallback_reason = terminal.reason_code
    return patch


def qa_result_to_patch(result: QAResult, *, next_node: str) -> StatePatch:
    """把 QA exact/RAG/fallback 结果映射为 StatePatch。"""
    candidates = [
        RetrievalEvidence(
            document_id=str(item.metadata.get("document_id") or item.chunk_id),
            chunk_id=item.chunk_id,
            title=item.title,
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
            metadata=item.metadata,
        )
        for item in result.trace_documents
    ]
    scores = [item.rerank_score for item in candidates]
    top_score = scores[0] if scores else None
    score_margin = (
        top_score - scores[1]
        if top_score is not None and len(scores) > 1 and scores[1] is not None
        else top_score
    )
    counts = result.retrieval_counts
    patch = StatePatch(
        context={"faq_hit": result.route == "faq"},
        retrieval=RetrievalState(
            status="available" if candidates else "empty",
            candidates=candidates,
            dense_count=max(0, counts.get("dense", 0)),
            bm25_count=max(0, counts.get("bm25", 0)),
            fusion_count=max(0, counts.get("fusion", 0)),
            rerank_count=max(0, counts.get("rerank", 0)),
            top_score=top_score,
            score_margin=score_margin,
            evidence_sufficient=result.route == "rag" and bool(candidates),
        ),
        evidence=candidates,
        route=result.route,
        final_answer=result.answer,
        fallback_reason="qa_no_evidence" if result.route == "fallback" else None,
        next_node=next_node,
        reply=AgentReplyState(
            source="qa",
            route=result.route,
            answer=result.answer,
            confidence=result.confidence,
            qa_hit=result.route != "fallback",
            reason_code=(
                result.decision_factors.get("faq_match_reason")
                if isinstance(result.decision_factors.get("faq_match_reason"), str)
                else None
            ),
            sources=[
                AgentSourceState(
                    chunk_id=source.chunk_id,
                    title=source.title,
                    source=source.source,
                    score=source.score,
                )
                for source in result.sources
            ],
        ),
    )
    return patch


__all__ = ["qa_result_to_patch", "rule_result_to_patch"]
