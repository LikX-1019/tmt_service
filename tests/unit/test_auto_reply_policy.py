import json

import pytest

from app.qa.models import QAResult, RetrievalDocument
from app.services.auto_reply_policy import (
    AutoReplyPolicy,
    Calibration,
    choose_calibration,
    wilson_lower_bound,
)


def result(*, route: str = "faq", risk: str = "低", product: str = "通用") -> QAResult:
    document = RetrievalDocument(
        chunk_id="QA-1",
        content="answer",
        metadata={
            "answer": "这是已审核的标准回答",
            "risk_level": risk,
            "product_id": product,
            "product_name": "助眠眼罩",
            "active": True,
            "review_status": "usable",
            "retrieval_enabled": True,
            "auto_reply_eligible": True,
            "human_required": False,
        },
        rerank_score=0.9,
    )
    return QAResult(answer="生成建议", route=route, trace_documents=[document])


def test_faq_auto_send_uses_standard_answer() -> None:
    decision = AutoReplyPolicy(Calibration()).evaluate(
        "怎么使用", result(), {"goods_name": None}, allow_auto=True
    )
    assert decision.action == "auto_send"
    assert decision.answer == "这是已审核的标准回答"


@pytest.mark.parametrize("query", ["订单发货了吗", "可以保证疗效吗", "我要投诉退款"])
def test_sensitive_question_always_routes_to_human(query: str) -> None:
    decision = AutoReplyPolicy(Calibration()).evaluate(
        query, result(), {"goods_name": None}, allow_auto=True
    )
    assert decision.action == "suggest"
    assert "人工" in (decision.reason or "")


def test_product_specific_answer_requires_matching_goods_context() -> None:
    decision = AutoReplyPolicy(Calibration()).evaluate(
        "重力珠位置固定吗",
        result(product="EYE-1"),
        {"goods_name": "另一款枕头"},
        allow_auto=True,
    )
    assert decision.action == "suggest"
    assert "商品" in (decision.reason or "")


def test_unknown_risk_never_auto_sends() -> None:
    decision = AutoReplyPolicy(Calibration()).evaluate(
        "普通问题", result(risk=""), {"goods_name": None}, allow_auto=True
    )
    assert decision.action == "suggest"
    assert "风险等级" in (decision.reason or "")


def test_rag_requires_calibration_thresholds() -> None:
    calibrated = Calibration(
        enabled=True,
        sample_count=100,
        accepted_count=90,
        precision=0.99,
        score_threshold=0.8,
        margin_threshold=0.2,
    )
    decision = AutoReplyPolicy(calibrated).evaluate(
        "普通问题", result(route="rag"), {"goods_name": None}, allow_auto=True
    )
    assert decision.action == "auto_send"


def test_calibration_requires_at_least_one_hundred_samples(tmp_path) -> None:
    rows = [
        {"top_score": 0.9, "margin": 0.4, "expected_qa": "a", "predicted_qa": "a"}
        for _ in range(99)
    ]
    assert choose_calibration(rows, min_precision=0.98, min_samples=100).enabled is False

    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"enabled": True, "sample_count": 99, "accepted_count": 99, "precision": 1, "score_threshold": 0.8, "margin_threshold": 0.2}))
    assert Calibration.load(path, min_precision=0.98, min_samples=100).enabled is False


def test_wilson_lower_bound_is_conservative() -> None:
    assert wilson_lower_bound(100, 100) == pytest.approx(0.963, abs=0.001)
    assert wilson_lower_bound(0, 0) == 0


def test_unreviewed_or_ineligible_knowledge_never_auto_sends() -> None:
    pending = result()
    pending.trace_documents[0].metadata["review_status"] = "pending_review"
    assert AutoReplyPolicy(Calibration()).evaluate(
        "怎么使用", pending, {}, allow_auto=True
    ).reason_code == "knowledge_not_reviewed"

    ineligible = result()
    ineligible.trace_documents[0].metadata["auto_reply_eligible"] = False
    assert AutoReplyPolicy(Calibration()).evaluate(
        "怎么使用", ineligible, {}, allow_auto=True
    ).reason_code == "auto_reply_ineligible"
