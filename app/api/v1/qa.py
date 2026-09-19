"""独立 QA API，兼容无会话检索与带商品上下文的 QA 客户端。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_chat_service, get_qa_service
from app.qa.models import QAResult, RetrievalDocument
from app.qa.service import QAService
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.common import ApiResponse
from app.schemas.qa import (
    QADemoResponse,
    QADemoSource,
    QARequest,
    QAResponse,
    QASource,
)


router = APIRouter(tags=["qa"])


async def _answer(
    service: QAService,
    request: QARequest,
    chat_service: Annotated[object, Depends(get_chat_service)],
) -> QAResult | ChatResponse:
    if request.conversation_id:
        return await chat_service.chat(
            ChatRequest(
                conversation_id=request.conversation_id,
                message=request.query,
                service_stage=request.service_stage,
            )
        )
    context = {
        key: value
        for key, value in {
            "product_code": request.product_code,
            "product_name": request.product_name,
            "service_stage": request.service_stage,
            "knowledge_version": request.knowledge_version,
        }.items()
        if value is not None
    }
    return await service.answer(request.query, **context)


def _build_qa_response(result: QAResult | ChatResponse) -> QAResponse:
    if isinstance(result, ChatResponse):
        return QAResponse(
            answer=result.answer,
            route=result.route if result.route in {
                "faq",
                "rag",
                "fallback",
                "product",
                "product_selection",
                "product_not_found",
                "product_missing",
                "product_link_invalid",
                "product_link_unsupported",
                "greeting",
                "human",
                "small_talk",
                "empathy",
            } else "fallback",
            confidence=result.confidence,
            product=result.product,
            products=result.products,
            product_resolution=result.product_resolution,
        )
    return QAResponse(
        answer=result.answer,
        route=result.route,
        sources=[
            QASource(
                chunk_id=source.chunk_id,
                title=source.title,
                source=source.source,
                score=source.score,
            )
            for source in result.sources
        ],
        confidence=result.confidence,
        match_score=result.match_score,
        auto_reply_confidence=result.auto_reply_confidence,
    )


def _display_answer(document: RetrievalDocument) -> str:
    answer = document.metadata.get("answer")
    if answer:
        return str(answer)
    marker = "\n回答："
    if marker in document.content:
        return document.content.split(marker, maxsplit=1)[1].strip()
    return document.content


@router.post("/qa", response_model=ApiResponse[QAResponse])
async def answer_question(
    request: QARequest,
    service: Annotated[QAService, Depends(get_qa_service)],
    chat_service: Annotated[object, Depends(get_chat_service)],
) -> ApiResponse[QAResponse]:
    result = await _answer(service, request, chat_service)
    return ApiResponse(data=_build_qa_response(result))


@router.post("/qa/demo", response_model=ApiResponse[QADemoResponse])
async def answer_question_with_trace(
    request: QARequest,
    service: Annotated[QAService, Depends(get_qa_service)],
    chat_service: Annotated[object, Depends(get_chat_service)],
) -> ApiResponse[QADemoResponse]:
    """返回答案与真实召回 QA 对；带会话时走统一 Chat 且没有检索 trace。"""
    result = await _answer(service, request, chat_service)
    public = _build_qa_response(result)
    if isinstance(result, ChatResponse):
        return ApiResponse(
            data=QADemoResponse(
                **public.model_dump(),
                retrieval_counts={},
                recalled_pairs=[],
            )
        )
    recalled_pairs = [
        QADemoSource(
            rank=rank,
            chunk_id=document.chunk_id,
            question=str(document.metadata.get("question") or document.title or "未命名问题"),
            answer=_display_answer(document),
            product_name=(
                str(document.metadata["product_name"])
                if document.metadata.get("product_name")
                else None
            ),
            source=document.source,
            dense_score=document.dense_score,
            bm25_score=document.bm25_score,
            fusion_score=document.fusion_score,
            rerank_score=document.rerank_score,
        )
        for rank, document in enumerate(result.trace_documents, start=1)
    ]
    return ApiResponse(
        data=QADemoResponse(
            **public.model_dump(),
            retrieval_counts=result.retrieval_counts,
            recalled_pairs=recalled_pairs,
        )
    )
