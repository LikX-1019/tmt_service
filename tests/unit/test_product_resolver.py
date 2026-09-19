import pytest

from app.core.config import Settings
from app.services.product_resolver import (
    ConversationProductReference,
    ProductResolution,
    ProductResolver,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("product_id=1001 适合跑步吗", "1001"),
        ("product id: 1001", "1001"),
        ("商品ID：972793561880", "972793561880"),
        ("商品 1001 怎么样", "1001"),
        ("TEST-WRIST-001 这个商品你能给我介绍一下吗", "TEST-WRIST-001"),
        ("1001 适合跑步吗", "1001"),
        ("TEST-WRIST-001", "TEST-WRIST-001"),
    ],
)
def test_extract_supported_product_id_formats(message: str, expected: str) -> None:
    assert ProductResolver.extract_from_message(message) == expected


def test_extract_rejects_ordinary_product_words_as_id() -> None:
    assert ProductResolver.extract_from_message("这个商品是什么材料") is None
    assert ProductResolver.extract_from_message("iphone16 有什么特点") is None
    assert ProductResolver.extract_from_message("2026 年什么时候发货") is None


def test_resolve_prefers_request_then_message_then_conversation() -> None:
    resolver = ProductResolver()
    assert resolver.resolve(
        request_product_id="1001",
        message="商品 2002",
        conversation_product_id="3003",
    ).source == "request"
    assert resolver.resolve(
        request_product_id=None,
        message="商品 2002",
        conversation_product_id="3003",
    ).source == "message_id"
    assert resolver.resolve(
        request_product_id=None,
        message="它适合跑步吗",
        conversation_product_id="3003",
    ).source == "conversation"
    assert resolver.resolve(
        request_product_id=None,
        message="你们什么时候发货",
        conversation_product_id=None,
    ).source == "none"


def test_resolve_known_product_url_before_message_and_conversation() -> None:
    resolver = ProductResolver(
        Settings(_env_file=None, product_api_base_url="https://products.example.test")
    )

    result = resolver.resolve(
        request_product_id=None,
        message="https://products.example.test/products/sku-1 这个商品有什么特点？",
        conversation_product_id="old-product",
    )

    assert result.product_id == "sku-1"
    assert result.source == "url"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("JAFFICK运动护膝适合跑步吗？", "jaffick运动护膝"),
        ("我想问一下 XX护膝", "xx护膝"),
        ("一天戴多久？", None),
        ("一天用多久？", None),
        ("它适合什么场景？", None),
        ("你们什么时候发货？", None),
    ],
)
def test_extract_name_query_is_conservative(message: str, expected: str | None) -> None:
    assert ProductResolver.extract_name_query(message, product_intent=False) == expected


def test_external_url_is_not_treated_as_a_product_identifier() -> None:
    resolver = ProductResolver(
        Settings(_env_file=None, product_api_base_url="https://products.example.test")
    )

    result = resolver.resolve(
        request_product_id=None,
        message="https://external.example/products/1001 这个商品怎么样？",
        conversation_product_id=None,
    )

    assert result.product_id is None
    assert result.source == "none"
    assert resolver.has_url("https://external.example/products/1001")


def test_attribute_questions_keep_current_or_explicit_product() -> None:
    resolver = ProductResolver()
    assert resolver.resolve(
        request_product_id=None,
        message="他有几个型号",
        conversation_product_id="TEST-WRIST-001",
    ).source == "conversation"
    assert resolver.resolve(
        request_product_id=None,
        message="TEST-WRIST-001 这个护腕他有几个型号啊",
        conversation_product_id=None,
    ).product_id == "TEST-WRIST-001"
    assert resolver.resolve(
        request_product_id=None,
        message="TEST-WRIST-001 他有l",
        conversation_product_id=None,
    ).product_id == "TEST-WRIST-001"


def test_historical_reference_resolves_named_recent_product() -> None:
    resolver = ProductResolver()
    recent = [
        ConversationProductReference("TEST-BOTTLE-002", "测试商品-运动水壶"),
        ConversationProductReference("TEST-WRIST-001", "测试商品-运动护腕"),
    ]

    result = resolver.resolve(
        request_product_id=None,
        message="刚刚那个护腕他有几个型号啊",
        conversation_product_id="TEST-BOTTLE-002",
        recent_products=recent,
    )

    assert result == ProductResolution("TEST-WRIST-001", "history")


def test_short_switch_reference_resolves_named_recent_product() -> None:
    resolver = ProductResolver()
    recent = [
        ConversationProductReference("TEST-WRIST-001", "测试商品-运动护腕"),
        ConversationProductReference("TEST-BOTTLE-002", "测试商品-运动水壶"),
    ]

    result = resolver.resolve(
        request_product_id=None,
        message="那水壶呢",
        conversation_product_id="TEST-WRIST-001",
        recent_products=recent,
    )

    assert result == ProductResolution("TEST-BOTTLE-002", "history")


def test_question_tail_is_not_treated_as_product_name() -> None:
    assert (
        ProductResolver.extract_name_query(
            "这个运动护腕我能使用吗", product_intent=True
        )
        == "运动护腕"
    )
