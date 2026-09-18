from typing import Any

import pytest

from app.core.exceptions import ProductServiceUnavailableError
from app.qa.models import QAResult, QASource
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.product_service import (
    ProductAnswer,
    ProductLookupError,
    ProductNotFoundError,
    ProductProfile,
)

pytestmark = pytest.mark.asyncio


class FakeConversations:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, str]] = {}

    async def get_binding(self, conversation_id: str) -> Any:
        value = self.values.get(conversation_id)
        if value is None:
            return None
        return type("Binding", (), {"product_id": value[0], "product_name": value[1]})()

    async def bind_product(
        self, conversation_id: str, *, product_id: str, product_name: str
    ) -> None:
        self.values[conversation_id] = (product_id, product_name)

    async def clear_binding(self, conversation_id: str) -> None:
        self.values.pop(conversation_id, None)


class FakeProducts:
    def __init__(self, products: dict[str, ProductProfile]) -> None:
        self.products = products
        self.calls: list[str] = []

    async def get_product_by_id(self, product_id: str) -> ProductProfile:
        self.calls.append(product_id)
        if product_id in self.products:
            return self.products[product_id]
        raise ProductNotFoundError(product_id)


class FailingProducts(FakeProducts):
    async def get_product_by_id(self, product_id: str) -> ProductProfile:
        self.calls.append(product_id)
        raise ProductLookupError("provider unavailable")


class FakeProductAnswers:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, query: str, product: ProductProfile, **_: Any) -> ProductAnswer:
        self.calls.append(product.id)
        return ProductAnswer(
            answer=f"{product.name}资料回答",
            facts_supported=True,
            contains_sensitive_or_after_sales=False,
            needs_clarification=False,
            confidence=0.98,
        )


class FakeQA:
    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, *_args: Any, **_kwargs: Any) -> QAResult:
        self.calls += 1
        return QAResult(
            answer="QA 回答",
            route="faq",
            confidence=1.0,
            sources=[QASource(chunk_id="QA-1", title="发货", source="cs_qa")],
        )


def make_service(
    *,
    products: FakeProducts | None = None,
    conversations: FakeConversations | None = None,
) -> tuple[ChatService, FakeProducts, FakeProductAnswers, FakeQA, FakeConversations]:
    products = products or FakeProducts(
        {
            "1001": ProductProfile(id="1001", name="护腕", summary="日常支撑。"),
            "2002": ProductProfile(id="2002", name="护膝", summary="运动支撑。"),
        }
    )
    conversations = conversations or FakeConversations()
    answers = FakeProductAnswers()
    qa = FakeQA()

    async def qa_provider() -> FakeQA:
        return qa

    service = ChatService(
        conversation_repository=conversations,
        product_repository=products,
        product_answer_service=answers,
        qa_provider=qa_provider,
    )
    return service, products, answers, qa, conversations


async def test_greeting_rule_is_terminal_even_with_bound_product() -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("1001", "护腕")
    service, products, answers, qa, _ = make_service(conversations=conversations)

    result = await service.chat(ChatRequest(conversation_id="c1", message="你好", product_id="1001"))

    assert (result.source, result.route, result.rule_name) == ("rule", "greeting", "GreetingRule")
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 0


async def test_after_sale_rule_is_terminal_even_with_explicit_product() -> None:
    service, products, answers, qa, _ = make_service()

    result = await service.chat(
        ChatRequest(conversation_id="c1", message="我要退款", product_id="1001")
    )

    assert result.source == "rule"
    assert result.rule_name == "AfterSaleRiskRule"
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 0


async def test_explicit_request_product_is_validated_bound_and_answered() -> None:
    service, products, answers, qa, conversations = make_service()

    result = await service.chat(
        ChatRequest(
            conversation_id="c1",
            message="适合跑步吗？",
            product_id="1001",
            service_stage="pre_sale",
        )
    )

    assert result.source == "product"
    assert result.product is not None
    assert (result.product.id, result.product.name) == ("1001", "护腕")
    assert result.product_resolution == "request"
    assert products.calls == ["1001"]
    assert answers.calls == ["1001"]
    assert qa.calls == 0
    assert conversations.values["c1"] == ("1001", "护腕")


async def test_message_product_id_binds_conversation() -> None:
    service, _, _, qa, conversations = make_service()

    result = await service.chat(
        ChatRequest(conversation_id="c1", message="商品ID：1001 这个怎么样")
    )

    assert result.product_resolution == "message"
    assert result.source == "product"
    assert conversations.values["c1"] == ("1001", "护腕")
    assert qa.calls == 0


async def test_follow_up_uses_bound_product_without_new_id() -> None:
    service, products, answers, _, _ = make_service()
    await service.chat(
        ChatRequest(conversation_id="c1", message="商品 1001 适合跑步吗", product_id=None)
    )

    result = await service.chat(ChatRequest(conversation_id="c1", message="它适合跑步吗"))

    assert result.product_resolution == "conversation"
    assert products.calls == ["1001", "1001"]
    assert answers.calls == ["1001", "1001"]


async def test_conversation_can_switch_explicit_product() -> None:
    service, _, _, _, conversations = make_service()
    await service.chat(ChatRequest(conversation_id="c1", message="这个怎么样", product_id="1001"))
    result = await service.chat(ChatRequest(conversation_id="c1", message="那这个呢", product_id="2002"))

    assert result.product is not None
    assert result.product.id == "2002"
    assert conversations.values["c1"] == ("2002", "护膝")


async def test_bound_conversation_does_not_force_general_shipping_question_to_product() -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("1001", "护腕")
    service, products, answers, qa, _ = make_service(conversations=conversations)

    result = await service.chat(ChatRequest(conversation_id="c1", message="你们什么时候发货"))

    assert result.source == "qa"
    assert result.product_resolution == "none"
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 1


async def test_product_question_without_resolution_asks_for_product() -> None:
    service, products, answers, qa, conversations = make_service()

    result = await service.chat(ChatRequest(conversation_id="c1", message="这个适合跑步吗"))

    assert result.source == "product"
    assert result.route == "product_missing"
    assert result.product_resolution == "none"
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 0
    assert "c1" not in conversations.values


async def test_explicit_missing_product_does_not_fallback_to_qa_or_bind() -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("1001", "护腕")
    service, products, answers, qa, conversations = make_service(conversations=conversations)

    result = await service.chat(
        ChatRequest(conversation_id="c1", message="这个怎么样", product_id="999999")
    )

    assert result.route == "product_not_found"
    assert result.product is None
    assert products.calls == ["999999"]
    assert answers.calls == []
    assert qa.calls == 0
    assert "c1" not in conversations.values


async def test_product_provider_failure_is_explicitly_unavailable() -> None:
    products = FailingProducts({})
    service, _, _, qa, _ = make_service(products=products)

    with pytest.raises(ProductServiceUnavailableError):
        await service.chat(
            ChatRequest(conversation_id="c1", message="这个怎么样", product_id="1001")
        )
    assert qa.calls == 0
