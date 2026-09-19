from typing import Any

import pytest

from app.agent.chat_runtime import ChatStateRuntime
from app.repositories.chat_message_repository import ChatConversationMessageRepository
from app.services.semantic_product_resolver import SemanticProductResolution
from app.core.config import Settings
from app.core.exceptions import ProductServiceUnavailableError
from app.qa.models import QAResult, QASource
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.product_resolver import ProductResolver
from app.services.product_service import (
    ProductAnswer,
    ProductLookupError,
    ProductNotFoundError,
    ProductProfile,
    ProductSearchResult,
)

pytestmark = pytest.mark.asyncio


class FakeConversations:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, str]] = {}
        self.histories: dict[str, list[dict[str, str]]] = {}

    async def get_binding(self, conversation_id: str) -> Any:
        value = self.values.get(conversation_id)
        if value is None:
            return None
        return type(
            "Binding",
            (),
            {
                "product_id": value[0],
                "product_name": value[1],
                "recent_products": self.histories.get(conversation_id, []),
            },
        )()

    async def bind_product(
        self,
        conversation_id: str,
        *,
        product_id: str,
        product_name: str,
        recent_products: list[dict[str, str]] | None = None,
    ) -> None:
        self.values[conversation_id] = (product_id, product_name)
        self.histories[conversation_id] = list(recent_products or [])

    async def clear_binding(self, conversation_id: str) -> None:
        self.values.pop(conversation_id, None)
        self.histories.pop(conversation_id, None)


class FakeProducts:
    def __init__(self, products: dict[str, ProductProfile]) -> None:
        self.products = products
        self.calls: list[str] = []

    async def get_product_by_id(self, product_id: str) -> ProductProfile:
        self.calls.append(product_id)
        if product_id in self.products:
            return self.products[product_id]
        raise ProductNotFoundError(product_id)

    async def search_products_by_name(
        self, query: str, *, limit: int = 5
    ) -> list[ProductSearchResult]:
        normalized = query.casefold()
        matches = []
        for product in self.products.values():
            name = product.name.casefold()
            if normalized not in name:
                continue
            matches.append(
                ProductSearchResult(
                    id=product.id,
                    name=product.name,
                    summary=product.summary,
                    match_type="exact" if normalized == name else "contains",
                )
            )
        return matches[:limit]


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
    def __init__(self, result: QAResult | None = None) -> None:
        self.calls = 0
        self.result = result

    def match_exact(self, *_args: Any, **_kwargs: Any):
        return self.result

    async def retrieve_context_candidates(self, *_args: Any, **_kwargs: Any) -> list:
        self.calls += 1
        return []

    async def answer(self, *_args: Any, **_kwargs: Any) -> QAResult:
        self.calls += 1
        return self.result or QAResult(
            answer="QA 回答",
            route="faq",
            confidence=1.0,
            sources=[QASource(chunk_id="QA-1", title="发货", source="cs_qa")],
        )


class FakeSemanticProducts:
    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.calls: list[str] = []
        self.responses = responses or {}

    async def resolve(self, message: str, **_: Any) -> SemanticProductResolution:
        self.calls.append(message)
        product_id = self.responses.get(message)
        if product_id:
            return SemanticProductResolution(
                matched=True,
                product_id=product_id,
                confidence=0.95,
                reason_code="semantic_match",
            )
        return SemanticProductResolution(reason_code="no_match")


class OtherSocialRouter:
    async def classify(self, *_args: Any, **_kwargs: Any):
        from app.services.social_router import SocialDecision

        return SocialDecision(intent="other")


class FakeFallback:
    def __init__(self, answer: str = "QA 回答") -> None:
        self.calls: list[str] = []
        self.answer = answer

    async def generate(self, query: str, **_: Any):
        from app.services.contextual_fallback_service import ContextualFallbackAnswer

        self.calls.append(query)
        return ContextualFallbackAnswer(
            answer=self.answer,
            confidence=0.98,
            needs_human=False,
        )


def make_service(
    *,
    products: FakeProducts | None = None,
    conversations: FakeConversations | None = None,
    qa: FakeQA | None = None,
    state_runtime: ChatStateRuntime | None = None,
    messages: ChatConversationMessageRepository | None = None,
    fallback: FakeFallback | None = None,
    semantic_products: FakeSemanticProducts | None = None,
    social_router: Any | None = None,
) -> tuple[ChatService, FakeProducts, FakeProductAnswers, FakeQA, FakeConversations]:
    products = products or FakeProducts(
        {
            "1001": ProductProfile(id="1001", name="护腕", summary="日常支撑。"),
            "2002": ProductProfile(id="2002", name="护膝", summary="运动支撑。"),
            "3003": ProductProfile(
                id="3003", name="运动护膝 标准版", summary="标准运动支撑。"
            ),
            "4004": ProductProfile(
                id="4004", name="运动护膝 专业版", summary="专业运动支撑。"
            ),
        }
    )
    conversations = conversations or FakeConversations()
    answers = FakeProductAnswers()
    qa = qa or FakeQA()

    async def qa_provider() -> FakeQA:
        return qa

    service = ChatService(
        conversation_repository=conversations,
        product_repository=products,
        product_answer_service=answers,
        qa_provider=qa_provider,
        product_resolver=ProductResolver(
            Settings(_env_file=None, product_api_base_url="http://127.0.0.1:8088")
        ),
        state_runtime=state_runtime,
        message_repository=messages,
        fallback_service=fallback or FakeFallback(),
        semantic_product_resolver=semantic_products,
        social_router=social_router,
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

    assert result.product_resolution == "message_id"
    assert result.source == "product"
    assert conversations.values["c1"] == ("1001", "护腕")
    assert qa.calls == 0


async def test_leading_bare_product_id_binds_conversation() -> None:
    service, _, _, qa, conversations = make_service()

    result = await service.chat(
        ChatRequest(
            conversation_id="c1",
            message="1001 这个商品你能给我介绍一下吗",
        )
    )

    assert result.product_resolution == "message_id"
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


async def test_duration_followup_reloads_bound_product_context() -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("1001", "护腕")
    service, products, answers, qa, _ = make_service(conversations=conversations)

    result = await service.chat(ChatRequest(conversation_id="c1", message="一天戴多久？"))

    assert result.source == "product"
    assert result.product is not None
    assert result.product.id == "1001"
    assert result.product_resolution == "conversation"
    assert products.calls == ["1001"]
    assert answers.calls == ["1001"]
    assert qa.calls == 0


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

    assert result.source == "llm_fallback"
    assert result.product_resolution == "conversation"
    assert products.calls == ["1001"]
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
    assert conversations.values["c1"] == ("1001", "护腕")


async def test_known_product_url_is_validated_bound_and_answered() -> None:
    service, products, answers, qa, conversations = make_service()

    result = await service.chat(
        ChatRequest(
            conversation_id="c1",
            message="http://127.0.0.1:8088/products/1001 这个商品有什么特点？",
        )
    )

    assert result.source == "product"
    assert result.product_resolution == "url"
    assert products.calls == ["1001"]
    assert answers.calls == ["1001"]
    assert qa.calls == 0
    assert conversations.values["c1"] == ("1001", "护腕")


async def test_exact_product_name_binds_and_answers() -> None:
    service, products, answers, qa, conversations = make_service()

    result = await service.chat(
        ChatRequest(conversation_id="c1", message="我想看看 护膝")
    )

    assert result.source == "product"
    assert result.product_resolution == "name_exact"
    assert result.product is not None and result.product.id == "2002"
    assert products.calls == ["2002"]
    assert answers.calls == ["2002"]
    assert qa.calls == 0
    assert conversations.values["c1"] == ("2002", "护膝")


async def test_ambiguous_product_name_returns_candidates_without_binding() -> None:
    service, products, answers, qa, conversations = make_service()

    result = await service.chat(
        ChatRequest(conversation_id="c1", message="我想看看 运动护膝")
    )

    assert result.source == "product_selection"
    assert result.product_resolution == "name_candidates"
    assert [product.id for product in result.products] == ["3003", "4004"]
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 0
    assert "c1" not in conversations.values


async def test_explicit_product_name_without_match_does_not_fallback_to_qa() -> None:
    service, products, answers, qa, conversations = make_service()

    result = await service.chat(
        ChatRequest(conversation_id="c1", message="我想看看 不存在的测试商品XYZ")
    )

    assert result.source == "product"
    assert result.route == "product_not_found"
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 0
    assert "c1" not in conversations.values


async def test_external_product_url_is_rejected_without_qa_or_llm() -> None:
    service, products, answers, qa, _ = make_service()

    result = await service.chat(
        ChatRequest(
            conversation_id="c1",
            message="https://external.example/products/1001 这个商品怎么样？",
        )
    )

    assert result.source == "product"
    assert result.route == "product_link_unsupported"
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 0


async def test_qa_detour_preserves_product_for_later_follow_up() -> None:
    service, products, answers, qa, conversations = make_service()
    await service.chat(
        ChatRequest(conversation_id="c1", message="这个怎么样", product_id="2002")
    )

    qa_result = await service.chat(
        ChatRequest(conversation_id="c1", message="你们什么时候发货")
    )
    product_result = await service.chat(
        ChatRequest(conversation_id="c1", message="那这个商品还有什么注意事项？")
    )

    assert qa_result.source == "llm_fallback"
    assert product_result.source == "product"
    assert product_result.product_resolution == "conversation"
    assert conversations.values["c1"] == ("2002", "护膝")
    assert products.calls == ["2002", "2002", "2002"]
    assert answers.calls == ["2002", "2002"]
    assert qa.calls == 1


async def test_unknown_product_attribute_uses_bound_product_context() -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("2002", "护膝")
    service, products, answers, qa, _ = make_service(conversations=conversations)

    result = await service.chat(
        ChatRequest(conversation_id="c1", message="这个商品的防水等级是多少？")
    )

    assert result.source == "product"
    assert result.product_resolution == "conversation"
    assert products.calls == ["2002"]
    assert answers.calls == ["2002"]
    assert qa.calls == 0


async def test_product_provider_failure_is_explicitly_unavailable() -> None:
    products = FailingProducts({})
    service, _, _, qa, _ = make_service(products=products)

    with pytest.raises(ProductServiceUnavailableError):
        await service.chat(
            ChatRequest(conversation_id="c1", message="这个怎么样", product_id="1001")
        )
    assert qa.calls == 0


def _mat_and_bottle_products() -> FakeProducts:
    return FakeProducts(
        {
            "TEST-MAT-003": ProductProfile(
                id="TEST-MAT-003",
                name="测试商品-瑜伽垫",
                summary="瑜伽支撑。",
                specifications={"厚度": "8mm", "尺寸": "183cm × 61cm"},
            ),
            "TEST-BOTTLE-002": ProductProfile(
                id="TEST-BOTTLE-002",
                name="测试商品-运动水壶",
                summary="运动补水。",
                specifications={"容量": "500ml"},
            ),
        }
    )


def _wrist_and_bottle_products() -> FakeProducts:
    return FakeProducts(
        {
            "TEST-WRIST-001": ProductProfile(
                id="TEST-WRIST-001",
                name="测试商品-运动护腕",
                summary="运动支撑。",
                specifications={"尺码": "S、M、L"},
            ),
            "TEST-BOTTLE-002": ProductProfile(
                id="TEST-BOTTLE-002",
                name="测试商品-运动水壶",
                summary="运动补水。",
            ),
        }
    )


async def test_bound_product_fallback_uses_context_without_keyword_route() -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("TEST-BOTTLE-002", "测试商品-运动水壶")
    fallback = FakeFallback("可以装，耐温范围覆盖 80 度。")
    service, products, answers, qa, _ = make_service(
        products=_wrist_and_bottle_products(),
        conversations=conversations,
        fallback=fallback,
    )

    response = await service.chat(
        ChatRequest(conversation_id="c1", message="80度的水可以装吗")
    )

    assert response.source == "llm_fallback"
    assert response.route == "llm_fallback"
    assert response.product is not None
    assert response.product.id == "TEST-BOTTLE-002"
    assert fallback.calls == ["80度的水可以装吗"]
    assert products.calls == ["TEST-BOTTLE-002"]
    assert answers.calls == []
    assert qa.calls == 1


async def test_qa_exact_match_precedes_rules_and_fallback() -> None:
    qa = FakeQA(
        QAResult(
            answer="标准 QA 答案",
            route="faq",
            confidence=1.0,
            sources=[QASource(chunk_id="QA-1", title="发货", source="cs_qa")],
        )
    )
    fallback = FakeFallback("不应使用")
    service, _, answers, _, _ = make_service(
        qa=qa,
        fallback=fallback,
    )

    response = await service.chat(
        ChatRequest(conversation_id="c1", message="你们什么时候发货")
    )

    assert response.source == "qa"
    assert response.route == "faq"
    assert response.answer == "标准 QA 答案"
    assert fallback.calls == []
    assert answers.calls == []


async def test_product_attribute_followups_keep_bound_product() -> None:
    service, _, answers, qa, _ = make_service(products=_wrist_and_bottle_products())

    first = await service.chat(
        ChatRequest(conversation_id="c1", message="TEST-WRIST-001 介绍一下")
    )
    followups = [
        "他有几个型号",
        "有M吗",
        "有哪些码",
        "最大码是什么",
        "商品型号是什么",
        "怎么使用",
        "有什么注意事项",
        "他有l",
        "他有L码的吗",
        "介绍一下这个产品",
        "目前多少钱",
        "s",
    ]

    for message in followups:
        response = await service.chat(ChatRequest(conversation_id="c1", message=message))
        assert response.route == "product"
        assert response.product is not None
        assert response.product.id == "TEST-WRIST-001"
        assert response.product_resolution == "conversation"

    assert first.route == "product"
    assert answers.calls == ["TEST-WRIST-001"] * (len(followups) + 1)
    assert qa.calls == 0


async def test_size_without_bound_product_asks_for_product_context() -> None:
    service, products, answers, qa, _ = make_service(
        products=_wrist_and_bottle_products()
    )

    response = await service.chat(ChatRequest(conversation_id="c1", message="s"))

    assert response.route == "product_missing"
    assert response.product is None
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 0


async def test_invalid_size_is_not_treated_as_product_id() -> None:
    conversations = FakeConversations()
    conversations.values["c1"] = ("TEST-WRIST-001", "测试商品-运动护腕")
    service, products, answers, qa, conversations = make_service(
        products=_wrist_and_bottle_products(),
        conversations=conversations,
    )

    for message in ("s", "S 我可以使用吗"):
        response = await service.chat(ChatRequest(conversation_id="c1", message=message))
        assert response.route == "product"
        assert response.product is not None
        assert response.product.id == "TEST-WRIST-001"

    assert products.calls == ["TEST-WRIST-001", "TEST-WRIST-001"]
    assert answers.calls == ["TEST-WRIST-001", "TEST-WRIST-001"]
    assert qa.calls == 0


async def test_direct_sku_attribute_question_reaches_product_facts() -> None:
    service, _, answers, qa, _ = make_service(products=_wrist_and_bottle_products())

    response = await service.chat(
        ChatRequest(
            conversation_id="c1",
            message="TEST-WRIST-001 这个护腕他有几个型号啊",
        )
    )

    assert response.route == "product"
    assert response.product is not None
    assert response.product.id == "TEST-WRIST-001"
    assert response.product_resolution == "message_id"
    assert answers.calls == ["TEST-WRIST-001"]
    assert qa.calls == 0


async def test_historical_product_reference_recovers_previous_product() -> None:
    service, _, answers, qa, conversations = make_service(
        products=_wrist_and_bottle_products()
    )

    await service.chat(
        ChatRequest(conversation_id="c1", message="TEST-WRIST-001 介绍一下")
    )
    await service.chat(
        ChatRequest(conversation_id="c1", message="TEST-BOTTLE-002 介绍一下这个")
    )
    response = await service.chat(
        ChatRequest(
            conversation_id="c1",
            message="刚刚那个护腕他有几个型号啊",
        )
    )
    followup = await service.chat(
        ChatRequest(conversation_id="c1", message="刚刚那个护腕还有L吗")
    )
    bottle = await service.chat(ChatRequest(conversation_id="c1", message="那水壶呢"))

    assert response.route == "product"
    assert response.product is not None
    assert response.product.id == "TEST-WRIST-001"
    assert response.product_resolution == "history"
    assert followup.product is not None
    assert followup.product.id == "TEST-WRIST-001"
    assert bottle.route == "product"
    assert bottle.product is not None
    assert bottle.product.id == "TEST-BOTTLE-002"
    assert answers.calls == [
        "TEST-WRIST-001",
        "TEST-BOTTLE-002",
        "TEST-WRIST-001",
        "TEST-WRIST-001",
        "TEST-BOTTLE-002",
    ]
    assert qa.calls == 0
    assert conversations.values["c1"] == (
        "TEST-BOTTLE-002",
        "测试商品-运动水壶",
    )


async def test_unique_name_contains_match_binds_product() -> None:
    service, _, answers, qa, _ = make_service(products=_wrist_and_bottle_products())

    response = await service.chat(
        ChatRequest(conversation_id="c1", message="这个运动护腕我能使用吗")
    )

    assert response.route == "product"
    assert response.product is not None
    assert response.product.id == "TEST-WRIST-001"
    assert response.product_resolution == "name_unique_contains"
    assert answers.calls == ["TEST-WRIST-001"]
    assert qa.calls == 0


async def test_multiple_name_contains_matches_require_selection() -> None:
    products = FakeProducts(
        {
            "wrist-basic": ProductProfile(
                id="wrist-basic", name="运动护腕 标准版", summary="标准支撑。"
            ),
            "wrist-pro": ProductProfile(
                id="wrist-pro", name="运动护腕 专业版", summary="专业支撑。"
            ),
        }
    )
    service, _, answers, _, _ = make_service(products=products)

    response = await service.chat(
        ChatRequest(conversation_id="c1", message="这个运动护腕我能使用吗")
    )

    assert response.route == "product_selection"
    assert response.product_resolution == "name_candidates"
    assert len(response.products) == 2
    assert answers.calls == []



async def test_no_product_fallback_does_not_bind_none() -> None:
    conversations = FakeConversations()
    fallback = FakeFallback("无法获取当前日期。")
    service, products, answers, qa, conversations = make_service(
        conversations=conversations,
        fallback=fallback,
        social_router=OtherSocialRouter(),
    )

    response = await service.chat(
        ChatRequest(conversation_id="c1", message="今天星期几")
    )

    assert response.source == "llm_fallback"
    assert response.route == "llm_fallback"
    assert response.product is None
    assert products.calls == []
    assert answers.calls == []
    assert qa.calls == 1
    assert "c1" not in conversations.values


async def test_semantic_history_resolver_maps_water_cup_to_bottle() -> None:
    products = _mat_and_bottle_products()
    conversations = FakeConversations()
    semantic = FakeSemanticProducts(
        {"那这个水杯的容量呢": "TEST-BOTTLE-002"}
    )
    service, products, answers, qa, conversations = make_service(
        products=products,
        conversations=conversations,
        semantic_products=semantic,
    )

    await service.chat(
        ChatRequest(conversation_id="c1", message="TEST-MAT-003 介绍一下")
    )
    await service.chat(
        ChatRequest(conversation_id="c1", message="TEST-BOTTLE-002 介绍一下")
    )
    await service.chat(
        ChatRequest(conversation_id="c1", message="刚刚那个瑜伽垫的厚度是多少啊")
    )
    response = await service.chat(
        ChatRequest(conversation_id="c1", message="那这个水杯的容量呢")
    )

    assert response.source == "product"
    assert response.route == "product"
    assert response.product is not None
    assert response.product.id == "TEST-BOTTLE-002"
    assert response.product_resolution == "history_semantic"
    assert semantic.calls == ["那这个水杯的容量呢"]
    assert answers.calls[-1] == "TEST-BOTTLE-002"
    assert conversations.values["c1"] == (
        "TEST-BOTTLE-002",
        "测试商品-运动水壶",
    )
    assert qa.calls == 0
