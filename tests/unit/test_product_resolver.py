import pytest

from app.services.product_resolver import ProductResolver


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("product_id=1001 适合跑步吗", "1001"),
        ("product id: 1001", "1001"),
        ("商品ID：972793561880", "972793561880"),
        ("商品 1001 怎么样", "1001"),
    ],
)
def test_extract_supported_product_id_formats(message: str, expected: str) -> None:
    assert ProductResolver.extract_from_message(message) == expected


def test_extract_rejects_ordinary_product_words_as_id() -> None:
    assert ProductResolver.extract_from_message("这个商品是什么材料") is None


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
    ).source == "message"
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
