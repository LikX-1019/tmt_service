"""与 FastAPI 解耦的 QA 唯一业务入口。"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from app.qa.evidence_checker import EvidenceChecker
from app.qa.faq_matcher import FAQMatcher
from app.qa.models import QAResult, QASource, RetrievalDocument
from app.qa.normalizer import QueryNormalizer


logger = logging.getLogger(__name__)

FALLBACK_ANSWER = "目前知识库中暂无相关信息，请联系人工客服获取帮助。"


class QAService:
    def __init__(
        self,
        faq_matcher: FAQMatcher,
        retriever: Any,
        reranker: Any,
        evidence_checker: EvidenceChecker,
        answer_generator: Any,
        normalizer: QueryNormalizer | None = None,
        has_knowledge: bool = True,
    ) -> None:
        self._normalizer = normalizer or QueryNormalizer()
        self._has_knowledge = has_knowledge
        self._faq_matcher = faq_matcher
        self._retriever = retriever
        self._reranker = reranker
        self._evidence_checker = evidence_checker
        self._answer_generator = answer_generator

    async def answer(
        self,
        query: str,
        *,
        product_code: str | None = None,
        service_stage: str | None = None,
        knowledge_version: str | None = None,
        product_name: str | None = None,
    ) -> QAResult:
        faq_result = self.match_exact(
            query,
            product_code=product_code,
            service_stage=service_stage,
            knowledge_version=knowledge_version,
            product_name=product_name,
        )
        if faq_result is not None:
            return faq_result
        return await self.answer_rag(
            query,
            product_code=product_code,
            service_stage=service_stage,
            knowledge_version=knowledge_version,
            product_name=product_name,
        )

    def match_exact(
        self,
        query: str,
        *,
        product_code: str | None = None,
        service_stage: str | None = None,
        knowledge_version: str | None = None,
        product_name: str | None = None,
    ) -> QAResult | None:
        """返回唯一精确 QA 命中；调用方可在 RAG 前插入其他受控路由。"""
        started_at = perf_counter()
        faq_match = self._faq_matcher.resolve(
            self._normalizer.normalize(query),
            product_code=product_code,
            service_stage=service_stage,
            knowledge_version=knowledge_version,
            product_name=product_name,
        )
        faq = faq_match.item
        if faq is None:
            return None
        result = QAResult(
            answer=faq.answer,
            route="faq",
            confidence=1.0,
            match_score=faq_match.match_score,
            decision_factors={"faq_match_reason": "unique_exact_match"},
            trace_documents=[
                RetrievalDocument(
                    chunk_id=faq.id,
                    content=f"问题：{faq.question}\n回答：{faq.answer}",
                    title=faq.question,
                    source=str(faq.metadata.get("source") or "cs_qa"),
                    metadata={
                        **faq.metadata,
                        "question": faq.question,
                        "answer": faq.answer,
                    },
                    rerank_score=1.0,
                )
            ],
            retrieval_counts={"dense": 0, "bm25": 0, "fusion": 0, "rerank": 0},
        )
        self._log_result(started_at, result, faq_hit=True, query_length=len(query))
        return result

    async def answer_rag(
        self,
        query: str,
        *,
        product_code: str | None = None,
        service_stage: str | None = None,
        knowledge_version: str | None = None,
        product_name: str | None = None,
    ) -> QAResult:
        """跳过精确 FAQ，使用既有检索和证据门槛生成通用答案。"""
        started_at = perf_counter()
        if not self._has_knowledge:
            result = QAResult(
                answer=FALLBACK_ANSWER,
                route="fallback",
                decision_factors={"knowledge_base_empty": True},
                retrieval_counts={"dense": 0, "bm25": 0, "fusion": 0, "rerank": 0},
            )
            self._log_result(
                started_at,
                result,
                faq_hit=False,
                query_length=len(query),
            )
            return result
        faq_match = self._faq_matcher.resolve(
            self._normalizer.normalize(query),
            product_code=product_code,
            service_stage=service_stage,
            knowledge_version=knowledge_version,
            product_name=product_name,
        )

        documents = await self._retriever.retrieve(query.strip())
        reranked = await self._reranker.rerank(query.strip(), documents)
        counts = getattr(self._retriever, "last_counts", {})
        retrieval_counts = dict(counts) if isinstance(counts, dict) else {}
        retrieval_counts["rerank"] = len(reranked)
        if not self._evidence_checker.is_sufficient(reranked):
            result = QAResult(
                answer=FALLBACK_ANSWER,
                route="fallback",
                match_score=faq_match.match_score,
                decision_factors={"faq_match_reason": faq_match.reason},
                trace_documents=reranked,
                retrieval_counts=retrieval_counts,
            )
            self._log_result(
                started_at,
                result,
                faq_hit=False,
                query_length=len(query),
                reranked=reranked,
            )
            return result

        generation_fallback = False
        try:
            answer = await self._answer_generator.generate(query.strip(), reranked)
        except Exception:
            standard_answer = str(reranked[0].metadata.get("answer") or "").strip()
            if not standard_answer:
                raise
            generation_fallback = True
            answer = standard_answer
            logger.exception(
                "qa_answer_generation_failed_using_standard_answer",
                extra={
                    "event": "qa_answer_generation_failed_using_standard_answer",
                    "qa_code": reranked[0].chunk_id,
                },
            )
        sources = [
            QASource(
                chunk_id=document.chunk_id,
                title=document.title,
                source=document.source,
                score=document.rerank_score,
            )
            for document in reranked
        ]
        result = QAResult(
            answer=answer,
            route="rag",
            sources=sources,
            match_score=faq_match.match_score,
            decision_factors={
                "faq_match_reason": faq_match.reason,
                "generation_fallback": generation_fallback,
            },
            trace_documents=reranked,
            retrieval_counts=retrieval_counts,
        )
        self._log_result(
            started_at,
            result,
            faq_hit=False,
            query_length=len(query),
            reranked=reranked,
        )
        return result

    async def match_candidates(
        self,
        query: str,
        *,
        product_code: str | None = None,
        service_stage: str | None = None,
        knowledge_version: str | None = None,
    ) -> tuple[str, list[RetrievalDocument]]:
        """返回校准所需候选，不生成大模型回答。"""
        normalized_query = self._normalizer.normalize(query)
        faq = self._faq_matcher.match(
            normalized_query,
            product_code=product_code,
            service_stage=service_stage,
            knowledge_version=knowledge_version,
        )
        if faq is not None:
            return (
                "faq",
                [
                    RetrievalDocument(
                        chunk_id=faq.id,
                        content=f"问题：{faq.question}\n回答：{faq.answer}",
                        title=faq.question,
                        source=str(faq.metadata.get("source") or "cs_qa"),
                        metadata={
                            **faq.metadata,
                            "question": faq.question,
                            "answer": faq.answer,
                        },
                        rerank_score=1.0,
                    )
                ],
            )
        return "rag", await self.retrieve_rag_candidates(query)

    async def retrieve_rag_candidates(self, query: str) -> list[RetrievalDocument]:
        """绕过 FAQ 精确命中，用于离线评估 RAG 排序质量。"""
        documents = await self._retriever.retrieve(query.strip())
        return await self._reranker.rerank(query.strip(), documents)

    async def retrieve_context_candidates(self, query: str) -> list[RetrievalDocument]:
        """为 LLM 兜底提供参考资料；知识库为空时不阻断兜底链路。"""
        if not self._has_knowledge:
            return []
        return await self.retrieve_rag_candidates(query)

    def _log_result(
        self,
        started_at: float,
        result: QAResult,
        *,
        faq_hit: bool,
        query_length: int,
        reranked: list[Any] | None = None,
    ) -> None:
        counts = {} if faq_hit else getattr(self._retriever, "last_counts", {})
        if not isinstance(counts, dict):
            counts = {}
        logger.info(
            "qa_request_completed",
            extra={
                "event": "qa_request_completed",
                "qa_route": result.route,
                "faq_hit": faq_hit,
                "query_length": query_length,
                "dense_count": counts.get("dense", 0),
                "bm25_count": counts.get("bm25", 0),
                "fusion_count": counts.get("fusion", 0),
                "rerank_count": len(reranked or []),
                "top_rerank_score": (
                    reranked[0].rerank_score if reranked else None
                ),
                "latency_ms": round((perf_counter() - started_at) * 1000, 2),
            },
        )
