import httpx
import pytest

from app.core.config import Settings
from app.services.product_service import (
    HttpProductClient,
    ProductAnswer,
    ProductAnswerService,
    ProductContextBuilder,
    ProductLookupError,
    ProductNotFoundError,
    ProductProfile,
    ProductSearchResult,
)


@pytest.mark.asyncio
async def test_product_client_uses_bearer_auth_and_validates_profile() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "sku-1",
                    "name": "护腕",
                    "summary": "用于日常佩戴支撑。",
                    "selling_points": ["轻便"],
                    "specifications": {"材质": "锦纶"},
                }
            },
        )

    settings = Settings(
        _env_file=None,
        product_api_base_url="https://products.example.test/api",
        product_api_bearer_token="secret-token",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        product = await HttpProductClient(settings, client).get_product("sku-1")

    assert product.name == "护腕"
    assert seen == {"path": "/api/products/sku-1", "authorization": "Bearer secret-token"}


@pytest.mark.asyncio
async def test_product_client_rejects_mismatched_product_id() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "other", "name": "护腕", "summary": "说明"})

    settings = Settings(_env_file=None, product_api_base_url="https://products.example.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProductLookupError, match="不一致"):
            await HttpProductClient(settings, client).get_product("sku-1")


@pytest.mark.asyncio
async def test_product_client_turns_timeout_into_safe_lookup_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    settings = Settings(_env_file=None, product_api_base_url="https://products.example.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProductLookupError, match="暂不可用"):
            await HttpProductClient(settings, client).get_product("sku-1")


def test_product_auto_send_requires_every_safety_gate() -> None:
    settings = Settings(_env_file=None, product_auto_reply_min_confidence=0.95)
    safe = ProductAnswer(
        answer="这款采用锦纶材质。",
        facts_supported=True,
        contains_sensitive_or_after_sales=False,
        needs_clarification=False,
        confidence=0.95,
    )
    assert ProductAnswerService.can_auto_send(safe, allow_auto=True, settings=settings)
    assert not ProductAnswerService.can_auto_send(
        safe.model_copy(update={"needs_clarification": True}),
        allow_auto=True,
        settings=settings,
    )
    assert not ProductAnswerService.can_auto_send(safe, allow_auto=False, settings=settings)


@pytest.mark.asyncio
async def test_product_client_maps_published_404_to_not_found() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "商品不存在或尚未发布"})

    settings = Settings(_env_file=None, product_api_base_url="https://products.example.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProductNotFoundError, match="未找到对应商品"):
            await HttpProductClient(settings, client).get_product("missing")


def test_product_context_builder_only_outputs_real_non_empty_fields() -> None:
    product = ProductProfile(
        id="972793561880",
        name="护腕",
        summary="日常支撑。",
        specifications={"材质": "锦纶"},
    )
    context = ProductContextBuilder.build(product)
    assert "商品ID：\n972793561880" in context
    assert "规格：\n- 材质：锦纶" in context
    assert "使用方法" not in context
    assert "适合场景" not in context


@pytest.mark.asyncio
async def test_product_client_searches_published_profiles_with_limit() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "sku-1",
                        "name": "运动护膝 标准版",
                        "summary": "标准支撑。",
                        "internal_code": "KNEE-STD",
                        "specifications": {"版本": "标准版"},
                        "match_type": "contains",
                    }
                ]
            },
        )

    settings = Settings(
        _env_file=None,
        product_api_base_url="https://products.example.test",
        product_api_bearer_token="secret-token",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await HttpProductClient(settings, client).search_products("运动护膝", 5)

    assert results == [
        ProductSearchResult(
            id="sku-1",
            name="运动护膝 标准版",
            summary="标准支撑。",
            internal_code="KNEE-STD",
            specifications={"版本": "标准版"},
            match_type="contains",
        )
    ]
    assert seen == {"path": "/products", "query": "query=%E8%BF%90%E5%8A%A8%E6%8A%A4%E8%86%9D&limit=5"}
