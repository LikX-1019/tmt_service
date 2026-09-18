"""Rule Engine API 契约测试。"""

import httpx
import pytest

from main import app


@pytest.mark.asyncio
async def test_rules_api_returns_terminal_rule_decision() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/rules/evaluate",
            json={
                "message": " 这个不合适，我要退款！！ ",
                "session_id": "sess_test",
                "customer_id": "demo_customer_test",
                "current_product_id": "P10086",
                "service_stage": "post_sale",
                "channel": "customer_demo",
            },
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["normalized_message"] == "这个不合适,我要退款"
    assert [item["rule_name"] for item in payload["decisions"]] == [
        "ProductContextRule",
        "AfterSaleRiskRule",
    ]
    assert payload["terminal_decision"]["rule_name"] == "AfterSaleRiskRule"
    assert payload["terminal_decision"]["route"] == "human"
    assert payload["requires_product"] is True
    assert payload["requires_human"] is True


@pytest.mark.asyncio
async def test_rules_api_keeps_non_terminal_message_for_next_router() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/rules/evaluate",
            json={"message": "这个护膝适合跑步吗？"},
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["terminal_decision"] is None
    assert payload["requires_product"] is True


@pytest.mark.asyncio
async def test_rules_api_rejects_blank_message() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/v1/rules/evaluate", json={"message": "   "})

    assert response.status_code == 422
