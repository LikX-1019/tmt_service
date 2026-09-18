import httpx
import pytest

from app.api.dependencies import get_chat_service
from app.core.exceptions import LLMInvocationError
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from main import app


class StubChatService:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            conversation_id=request.conversation_id,
            answer=f"已收到：{request.message}",
            source="qa",
            route="stub",
        )


@pytest.mark.asyncio
async def test_chat_api_returns_uniform_response() -> None:
    app.dependency_overrides[get_chat_service] = lambda: StubChatService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/chat",
                json={"message": " 你好 "},
                headers={"X-Request-ID": "test-request-123"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-123"
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "conversation_id": None,
            "answer": "已收到：你好",
            "source": "qa",
            "route": "stub",
            "product": None,
            "product_resolution": "none",
            "rule_name": None,
            "reason_code": None,
            "confidence": None,
            "sources": [],
        },
    }


@pytest.mark.asyncio
async def test_chat_api_rejects_blank_message() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "   "})

    assert response.status_code == 422
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": "请求参数校验失败",
        "data": None,
    }


@pytest.mark.asyncio
async def test_chat_api_hides_llm_traceback() -> None:
    class BrokenChatService(ChatService):
        async def chat(self, request: ChatRequest) -> ChatResponse:
            raise LLMInvocationError()

    app.dependency_overrides[get_chat_service] = lambda: BrokenChatService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/chat",
                json={"message": "你好"},
                headers={"X-Request-ID": "failed-request-456"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.headers["X-Request-ID"] == "failed-request-456"
    assert response.json() == {
        "code": "LLM_INVOCATION_ERROR",
        "message": "大模型调用失败，请稍后重试",
        "data": None,
    }
    assert "private provider failure" not in response.text


@pytest.mark.asyncio
async def test_chat_api_accepts_conversation_and_product_context() -> None:
    class ProductStubService:
        async def chat(self, request: ChatRequest) -> ChatResponse:
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer="适合日常跑步。",
                source="product",
                route="product",
                product={"id": request.product_id, "name": "护膝"},
                product_resolution="request",
                confidence=0.98,
            )

    app.dependency_overrides[get_chat_service] = lambda: ProductStubService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/chat",
                json={
                    "conversation_id": "c1",
                    "product_id": "1001",
                    "message": "适合跑步吗？",
                    "service_stage": "pre_sale",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["conversation_id"] == "c1"
    assert data["source"] == "product"
    assert data["product"] == {"id": "1001", "name": "护膝"}
    assert data["product_resolution"] == "request"


@pytest.mark.asyncio
async def test_health() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_chat_page_is_served_by_fastapi() -> None:
    """确认首页已切换到客服控制台。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "智能客服工作台" in response.text
    assert "/assets/console/app.js" in response.text


@pytest.mark.asyncio
async def test_console_keeps_current_page_open_when_connector_starts() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/assets/console/app.js")

    assert response.status_code == 200
    assert 'api(`/connector/${action}`' in response.text
    assert "window.close()" not in response.text
    assert "launcher-mode" not in response.text


@pytest.mark.asyncio
async def test_chat_demo_remains_available() -> None:
    """确认原聊天测试页仍可从独立地址访问。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/chat-demo")

    assert response.status_code == 200
    assert "小满客服" in response.text
    assert 'const API_URL = "/api/v1/chat"' in response.text


@pytest.mark.asyncio
async def test_qa_demo_page_is_served_by_fastapi() -> None:
    """确认真实 QA 召回演示页由 FastAPI 提供。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/qa-demo")

    assert response.status_code == 200
    assert 'const API_URL = "/api/v1/qa/demo"' in response.text
