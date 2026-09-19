import httpx
import pytest

from app.core.exceptions import LLMInvocationError

from app.api.dependencies import get_chat_service, get_qa_service
from app.qa.models import QAResult, RetrievalDocument
from app.schemas.chat import ChatProductView, ChatResponse
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
            "product": None,
            "products": [],
            "product_resolution": None,
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
async def test_qa_api_sanitizes_provider_errors() -> None:
    class MilvusUnavailableQAService:
        async def answer(self, query: str) -> QAResult:
            raise RuntimeError(
                "pymilvus private endpoint 10.0.0.9:19530 connection refused"
            )

    app.dependency_overrides[get_qa_service] = lambda: MilvusUnavailableQAService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/v1/qa", json={"query": "清洗"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "服务器内部错误",
        "data": None,
    }
    assert "pymilvus" not in response.text
    assert "10.0.0.9" not in response.text


@pytest.mark.asyncio
async def test_qa_api_sanitizes_llm_provider_errors() -> None:
    class LLMBrokenQAService:
        async def answer(self, query: str) -> QAResult:
            raise LLMInvocationError("DeepSeek private API key rejected")

    app.dependency_overrides[get_qa_service] = lambda: LLMBrokenQAService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/qa", json={"query": "清洗"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {
        "code": "LLM_INVOCATION_ERROR",
        "message": "大模型调用失败，请稍后重试",
        "data": None,
    }
    assert "DeepSeek" not in response.text
    assert "private API key" not in response.text


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


@pytest.mark.asyncio
async def test_qa_api_with_conversation_uses_unified_chat_context() -> None:
    class StubChatService:
        async def chat(self, request):
            assert request.conversation_id == "sess-1"
            assert request.message == "TEST-WRIST-001 介绍一下"
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer="护腕介绍",
                source="product",
                route="product",
                product=ChatProductView(
                    id="TEST-WRIST-001", name="测试商品-运动护腕"
                ),
                product_resolution="message_id",
                confidence=0.99,
            )

    app.dependency_overrides[get_qa_service] = lambda: StubQAService()
    app.dependency_overrides[get_chat_service] = lambda: StubChatService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/qa",
                json={
                    "query": "TEST-WRIST-001 介绍一下",
                    "conversation_id": "sess-1",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["answer"] == "护腕介绍"
    assert data["route"] == "product"
    assert data["product"]["id"] == "TEST-WRIST-001"
    assert data["product_resolution"] == "message_id"
    assert data["sources"] == []
