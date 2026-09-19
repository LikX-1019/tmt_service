"""服务导航页与本地受控运维接口集成测试。"""

import httpx
import pytest
from httpx import ASGITransport

from app.schemas.ops import OpsActionResultView, OpsSnapshotView
from app.api.v1 import ops as ops_api
from main import app


@pytest.mark.asyncio
async def test_service_hub_page_is_served_by_fastapi() -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/service-hub")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "TMT 服务导航台" in response.text
    assert "页面入口" in response.text
    assert "Docker 依赖服务" in response.text
    assert "/assets/service-hub/app.css" in response.text
    assert "/assets/service-hub/app.js" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset_name", "content_type", "marker"),
    [
        ("app.css", "text/css", ".link-grid"),
        ("app.js", "text/javascript", "async function runServiceAction"),
        ("app.js", "text/javascript", "button.dataset.target = service.service_name"),
        ("app.js", "text/javascript", "application?.is_ok"),
    ],
)
async def test_service_hub_static_assets_are_served(
    asset_name: str,
    content_type: str,
    marker: str,
) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(f"/assets/service-hub/{asset_name}")

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert marker in response.text


@pytest.mark.asyncio
async def test_service_hub_page_does_not_use_top_level_await() -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/assets/service-hub/app.js")

    assert response.status_code == 200
    assert "await initialize();" not in response.text
    assert "void initialize();" in response.text


class FakeOpsService:
    async def build_local_service_snapshot(self) -> OpsSnapshotView:
        return OpsSnapshotView(application_pid=123)

    async def control_compose_services(self, *, target: str, action: str) -> OpsActionResultView:
        return OpsActionResultView(
            control_target=target,
            control_action=action,
            success=True,
            result_detail="fake compose result",
        )


@pytest.mark.asyncio
async def test_ops_snapshot_requires_local_client() -> None:
    original = ops_api._local_ops_service
    ops_api._local_ops_service = FakeOpsService()
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app, client=("192.168.1.10", 54321)),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/ops/snapshot")
    finally:
        ops_api._local_ops_service = original

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_ops_service_action_requires_local_header() -> None:
    original = ops_api._local_ops_service
    ops_api._local_ops_service = FakeOpsService()
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/ops/services",
                json={"target": "core", "action": "start"},
            )
    finally:
        ops_api._local_ops_service = original

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing local service hub header."


@pytest.mark.asyncio
async def test_ops_service_action_uses_whitelisted_payload() -> None:
    original = ops_api._local_ops_service
    ops_api._local_ops_service = FakeOpsService()
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/ops/services",
                headers={"X-Service-Hub": "local"},
                json={"target": "commodity-management", "action": "restart"},
            )
    finally:
        ops_api._local_ops_service = original

    assert response.status_code == 200
    assert response.json()["data"] == {
        "control_target": "commodity-management",
        "control_action": "restart",
        "success": True,
        "result_detail": "fake compose result",
    }
