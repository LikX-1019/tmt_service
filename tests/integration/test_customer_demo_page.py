"""Customer Demo 静态页面与路由集成测试。"""

import httpx
import pytest

from main import app


@pytest.mark.asyncio
async def test_customer_demo_page_is_served_by_fastapi() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/customer-demo")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "消费者侧聊天模拟器" in response.text
    assert "当前使用统一 Chat API，由后端决定规则、商品与 QA 路由。" in response.text
    assert "/assets/customer-demo/app.css" in response.text
    assert "/assets/customer-demo/app.js" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset_name", "content_type", "marker"),
    [
        ("app.css", "text/css", ".chat-panel"),
        ("app.js", "text/javascript", "class CustomerChatTransport"),
    ],
)
async def test_customer_demo_static_assets_are_served(
    asset_name: str,
    content_type: str,
    marker: str,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(f"/assets/customer-demo/{asset_name}")

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert marker in response.text


@pytest.mark.asyncio
async def test_customer_demo_layout_keeps_scrolling_inside_chat_panel() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/assets/customer-demo/app.css")

    assert response.status_code == 200
    assert "height: 100dvh" in response.text
    assert "overflow-y: auto" in response.text
    assert "overscroll-behavior: contain" in response.text
    assert "clamp(218px, 19vw, 300px)" in response.text
    assert "minmax(400px, 1fr)" in response.text


@pytest.mark.asyncio
async def test_customer_demo_js_supports_selectable_session_history() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/assets/customer-demo/app.js")

    assert response.status_code == 200
    assert "function renderSessionHistory" in response.text
    assert "function selectSession" in response.text
    assert 'item.addEventListener("click", () => selectSession(session.sessionId))' in response.text
    assert 'item.className = `session-item${session.sessionId === state.sessionId ? " active" : ""}`' in response.text
    assert "session-item" in response.text


@pytest.mark.asyncio
async def test_customer_demo_keeps_product_context_isolated_per_session() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/assets/customer-demo/app.js")

    assert response.status_code == 200
    assert 'explicitProductId: ""' in response.text
    assert 'state.explicitProductId = session.explicitProductId ?? ""' in response.text
    assert 'dom.productId.value = state.explicitProductId || state.boundProductId || ""' in response.text
    assert 'message.product_id = productIdOverride || state.explicitProductId' in response.text
    assert 'state.explicitProductId = ""' in response.text
    assert 'dom.productId.value = result.product.id' in response.text


@pytest.mark.asyncio
async def test_customer_demo_transport_uses_qa_compatibility_and_safe_rendering() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/assets/customer-demo/app.js")

    assert response.status_code == 200
    assert 'this.endpoint = "/api/v1/qa"' in response.text
    assert 'this.endpoint = "/api/v1/chat"' in response.text
    assert 'this.endpoint = "/api/v1/qa"' in response.text
    assert 'this.endpoint = "/api/v1/rag-chat/messages"' in response.text
    assert 'this.endpoint = "/api/v1/rules/evaluate"' in response.text
    assert "class RulePreflightTransport" in response.text
    assert "class QACompatibilityTransport" in response.text
    assert "conversation_id: message.session_id" in response.text
    assert 'debugConversationId: document.querySelector("#debugConversationId")' in response.text
    assert "product_id: message.product_id || null" in response.text
    assert "source: result.source" in response.text
    assert "product_resolution: result.productResolution" in response.text
    assert "function selectProductCandidate" in response.text
    assert "normalizeProducts(data.products)" in response.text
    assert "qa_hit: result.qaHit" in response.text
    assert "content.textContent = message.content" in response.text
    assert "innerHTML" not in response.text


@pytest.mark.asyncio
async def test_customer_demo_rejects_unknown_static_assets() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/assets/customer-demo/secret.txt")

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("/", "智能客服工作台"),
        ("/chat-demo", "小满客服"),
        ("/qa-demo", "QA 检索观测"),
    ],
)
async def test_existing_web_pages_remain_available(path: str, marker: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert marker in response.text
