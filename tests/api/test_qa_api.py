import httpx
import pytest

from app.api.dependencies import get_qa_service
from app.qa.models import QAResult, RetrievalDocument
from main import app


class StubQAService:
    async def answer(self, query: str) -> QAResult:
        return QAResult(
            answer=f"已查询：{query}",
            route="faq",
            confidence=1.0,
            trace_documents=[
                RetrievalDocument(
                    chunk_id="Q-DEMO-001",
                    content="支持七天无理由退货。",
                    title="退货规则",
                    source="cs_qa",
                    metadata={
                        "question": "可以退货吗？",
                        "answer": "支持七天无理由退货。",
                        "product_name": "通用售后",
                    },
                    dense_score=0.81,
                    bm25_score=3.2,
                    fusion_score=0.03,
                    rerank_score=0.94,
                )
            ],
            retrieval_counts={"dense": 10, "bm25": 10, "fusion": 15, "rerank": 5},
        )


@pytest.mark.asyncio
async def test_qa_api_returns_uniform_response() -> None:
    app.dependency_overrides[get_qa_service] = lambda: StubQAService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/qa", json={"query": " 退货规则 "})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "answer": "已查询：退货规则",
            "route": "faq",
            "sources": [],
            "confidence": 1.0,
            "match_score": None,
            "auto_reply_confidence": None,
        },
    }


@pytest.mark.asyncio
async def test_qa_api_rejects_blank_query() -> None:
    app.dependency_overrides[get_qa_service] = lambda: StubQAService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/qa", json={"query": "   "})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_qa_demo_api_returns_recalled_pairs_and_scores() -> None:
    app.dependency_overrides[get_qa_service] = lambda: StubQAService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/qa/demo", json={"query": "退货规则"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["retrieval_counts"] == {
        "dense": 10,
        "bm25": 10,
        "fusion": 15,
        "rerank": 5,
    }
    assert data["recalled_pairs"] == [
        {
            "rank": 1,
            "chunk_id": "Q-DEMO-001",
            "question": "可以退货吗？",
            "answer": "支持七天无理由退货。",
            "product_name": "通用售后",
            "source": "cs_qa",
            "dense_score": 0.81,
            "bm25_score": 3.2,
            "fusion_score": 0.03,
            "rerank_score": 0.94,
        }
    ]
