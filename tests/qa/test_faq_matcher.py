from app.qa.faq_matcher import FAQMatcher
from app.qa.models import FAQItem


def make_matcher() -> FAQMatcher:
    return FAQMatcher(
        [
            FAQItem(
                id="FAQ001",
                question="支持七天无理由退货吗",
                aliases=["可以七天无理由吗", "七天内可以退货吗"],
                answer="符合条件的商品支持七天无理由退货。",
            )
        ]
    )


def test_canonical_question_exact_match() -> None:
    assert make_matcher().match("支持七天无理由退货吗？").id == "FAQ001"


def test_alias_exact_match() -> None:
    assert make_matcher().match(" 可以七天无理由吗 ").id == "FAQ001"


def test_unknown_question_misses() -> None:
    assert make_matcher().match("你们公司的老板是谁") is None


def contextual_item(item_id: str, product: str | None, stage: str, answer: str) -> FAQItem:
    return FAQItem(
        id=item_id,
        question="材质是什么",
        answer=answer,
        metadata={"product_id": product, "service_stage": stage},
    )


def test_same_question_selects_product_and_stage() -> None:
    matcher = FAQMatcher([
        contextual_item("A-PRE", "A", "pre_sale", "A 售前"),
        contextual_item("A-GEN", "A", "general", "A 通用"),
        contextual_item("B-PRE", "B", "pre_sale", "B 售前"),
    ])

    assert matcher.match("材质是什么", product_code="A", service_stage="pre_sale").id == "A-PRE"
    assert matcher.match("材质是什么", product_code="A", service_stage="post_sale").id == "A-GEN"


def test_general_knowledge_fallback_respects_stage() -> None:
    matcher = FAQMatcher([
        contextual_item("COMMON-PRE", None, "pre_sale", "通用售前"),
        contextual_item("COMMON", None, "general", "通用"),
    ])

    assert matcher.match("材质是什么", product_code="A", service_stage="pre_sale").id == "COMMON-PRE"


def test_missing_product_context_does_not_choose_product_specific_answer() -> None:
    matcher = FAQMatcher([
        contextual_item("A", "A", "general", "A"),
        contextual_item("B", "B", "general", "B"),
    ])

    resolved = matcher.resolve("材质是什么")
    assert resolved.item is None
    assert resolved.reason == "missing_product_context"


def test_same_priority_exact_matches_are_ambiguous() -> None:
    matcher = FAQMatcher([
        contextual_item("A1", "A", "general", "一"),
        contextual_item("A2", "A", "general", "二"),
    ])

    resolved = matcher.resolve("材质是什么", product_code="A")
    assert resolved.item is None
    assert resolved.ambiguous is True


def test_product_code_matches_without_service_stage() -> None:
    matcher = FAQMatcher([
        contextual_item("A-PRE", "A", "pre_sale", "A 售前"),
        contextual_item("B-PRE", "B", "pre_sale", "B 售前"),
    ])

    resolved = matcher.resolve("材质是什么", product_code="A")
    assert resolved.item is not None
    assert resolved.item.id == "A-PRE"


def test_product_name_matches_when_platform_id_differs() -> None:
    item = FAQItem(
        id="BG07-MAT",
        question="材质是什么",
        answer="航空级铝合金。",
        metadata={"product_id": "BG07", "product_name": "护腕支架pro", "service_stage": "pre_sale"},
    )
    matcher = FAQMatcher([item, contextual_item("OTHER", "OTHER", "pre_sale", "其他")])

    resolved = matcher.resolve(
        "材质是什么",
        product_code="123456789",
        product_name="护腕支架pro 加长版",
    )
    assert resolved.item is not None
    assert resolved.item.id == "BG07-MAT"


def test_product_name_ambiguity_is_not_resolved() -> None:
    def named(item_id: str, name: str) -> FAQItem:
        return FAQItem(
            id=item_id,
            question="材质是什么",
            answer=f"{item_id} 回答",
            metadata={"product_id": item_id, "product_name": name, "service_stage": "pre_sale"},
        )

    matcher = FAQMatcher([named("A", "护腕支架"), named("B", "护腕支架pro")])
    resolved = matcher.resolve("材质是什么", product_name="护腕支架")
    assert resolved.item is None
    assert resolved.ambiguous is True
