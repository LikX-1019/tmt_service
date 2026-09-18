"""自动回复风险门控和 RAG 校准结果读取。"""

from __future__ import annotations

import json
import math
import re
from statistics import NormalDist
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.qa.models import QAResult


SENSITIVE_PATTERN = re.compile(
    r"退款|退货|赔付|赔偿|投诉|差评|平台介入|物流|快递到哪|订单|发货了吗|"
    r"疗效|治疗|康复|受伤|疼痛|疾病|医生|保证|一定有效"
)


@dataclass(slots=True)
class Calibration:
    enabled: bool = False
    sample_count: int = 0
    accepted_count: int = 0
    precision: float = 0.0
    precision_lower_bound: float = 0.0
    score_threshold: float = float("inf")
    margin_threshold: float = float("inf")

    @classmethod
    def load(cls, path: Path, *, min_precision: float, min_samples: int) -> Calibration:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            result = cls(
                enabled=bool(payload.get("enabled")),
                sample_count=int(payload.get("sample_count") or 0),
                accepted_count=int(payload.get("accepted_count") or 0),
                precision=float(payload.get("precision") or 0),
                precision_lower_bound=float(payload.get("precision_lower_bound") or 0),
                score_threshold=float(payload.get("score_threshold")),
                margin_threshold=float(payload.get("margin_threshold")),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return cls()
        result.enabled = (
            result.enabled
            and result.sample_count >= min_samples
            and result.precision_lower_bound >= min_precision
            and result.accepted_count > 0
        )
        return result


@dataclass(slots=True)
class PolicyDecision:
    route: str
    action: str
    answer: str | None
    qa_code: str | None
    top_score: float | None
    margin: float | None
    reason: str | None
    reason_code: str | None = None


def _normalize(value: str | None) -> str:
    return re.sub(r"\W+", "", (value or "").lower())


def wilson_lower_bound(correct: int, total: int, confidence: float = 0.95) -> float:
    """返回二项比例 Wilson 置信区间下界。"""
    if total <= 0:
        return 0.0
    if correct < 0 or correct > total:
        raise ValueError("correct must be between 0 and total")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    proportion = correct / total
    denominator = 1 + z * z / total
    centre = proportion + z * z / (2 * total)
    adjustment = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    )
    return max(0.0, (centre - adjustment) / denominator)


class AutoReplyPolicy:
    """自动发送只使用已审核标准回答，生成式结果仅作为人工建议。"""

    def __init__(
        self,
        calibration: Calibration,
        *,
        rag_mode: str = "calibrated",
        rag_score_threshold: float = 0.35,
        rag_min_margin: float = 0.10,
    ) -> None:
        if rag_mode not in {"suggest_only", "calibrated", "immediate"}:
            raise ValueError(f"不支持的 RAG 自动回复模式：{rag_mode}")
        if rag_min_margin < 0:
            raise ValueError("RAG 自动回复候选差值不能为负数")
        self._calibration = calibration
        self._rag_mode = rag_mode
        self._rag_score_threshold = rag_score_threshold
        self._rag_min_margin = rag_min_margin

    def evaluate(
        self,
        query: str,
        result: QAResult,
        conversation: dict[str, Any],
        *,
        allow_auto: bool,
    ) -> PolicyDecision:
        documents = result.trace_documents
        top = documents[0] if documents else None
        second = documents[1] if len(documents) > 1 else None
        metadata = top.metadata if top else {}
        score = top.rerank_score if top else None
        second_score = second.rerank_score if second else None
        margin = (
            score - second_score
            if score is not None and second_score is not None
            else score
        )
        qa_code = top.chunk_id if top else None
        standard_answer = str(metadata.get("answer") or "").strip() or None

        if result.route == "fallback" or top is None:
            return PolicyDecision(
                result.route,
                "suggest",
                result.answer,
                qa_code,
                score,
                margin,
                "知识证据不足",
                "fallback",
            )

        faq_reason = str(result.decision_factors.get("faq_match_reason") or "")
        if faq_reason in {"ambiguous_exact_match", "missing_product_context"}:
            return PolicyDecision(
                result.route, "suggest", result.answer, qa_code, score, margin,
                "FAQ 匹配存在商品上下文歧义", "ambiguous_product",
            )

        review_status = str(metadata.get("review_status") or "").lower()
        if review_status != "usable":
            return PolicyDecision(
                result.route, "suggest", result.answer, qa_code, score, margin,
                "知识尚未通过审核", "knowledge_not_reviewed",
            )
        if not bool(metadata.get("retrieval_enabled", False)):
            return PolicyDecision(
                result.route, "suggest", result.answer, qa_code, score, margin,
                "知识未启用检索", "knowledge_retrieval_disabled",
            )
        if bool(metadata.get("human_required")):
            return PolicyDecision(
                result.route, "suggest", result.answer, qa_code, score, margin,
                "知识要求人工处理", "human_required",
            )

        if metadata and not bool(metadata.get("active", True)):
            return PolicyDecision(
                result.route,
                "suggest",
                result.answer,
                qa_code,
                score,
                margin,
                "知识条目当前无效",
                "knowledge_inactive",
            )

        reason = self._risk_reason(query, metadata, conversation)
        if not reason and standard_answer:
            prohibited = [
                str(value).strip()
                for value in metadata.get("prohibited_expressions", [])
                if str(value).strip()
            ]
            if any(value in standard_answer for value in prohibited):
                reason = "标准回答命中知识库禁止表达"
        if reason:
            return PolicyDecision(
                result.route,
                "suggest",
                result.answer,
                qa_code,
                score,
                margin,
                reason,
                (
                    "product_context_missing"
                    if reason == "缺少匹配的商品卡片上下文"
                    else None
                ),
            )

        if result.route == "faq":
            if not standard_answer:
                return PolicyDecision(
                    result.route,
                    "suggest",
                    result.answer,
                    qa_code,
                    score,
                    margin,
                    "候选缺少标准回答",
                    "missing_standard_answer",
                )
            if not allow_auto:
                return PolicyDecision(
                    result.route,
                    "suggest",
                    result.answer,
                    qa_code,
                    score,
                    margin,
                    "自动回复未启用",
                )
            if not bool(metadata.get("auto_reply_eligible", False)):
                return PolicyDecision(
                    result.route,
                    "suggest",
                    result.answer,
                    qa_code,
                    score,
                    margin,
                    "知识未获自动发送资格",
                    "auto_reply_ineligible",
                )
            return PolicyDecision(
                result.route, "auto_send", standard_answer, qa_code, score, margin, None
            )

        if result.route == "rag":
            reason_code: str | None = None
            if not standard_answer:
                reason = "候选缺少标准回答"
                reason_code = "missing_standard_answer"
            elif self._rag_mode == "suggest_only":
                reason = "RAG 自动发送已关闭"
            elif self._rag_mode == "immediate":
                if score is None or score < self._rag_score_threshold:
                    reason = "RAG top1 分数不足"
                    reason_code = "low_rag_score"
                elif margin is None or margin < self._rag_min_margin:
                    reason = "RAG 候选区分度不足"
                    reason_code = "low_rag_margin"
                else:
                    reason = None
            elif not self._calibration.enabled:
                reason = "RAG 校准未达到自动发送门槛"
            elif score is None or score < self._calibration.score_threshold:
                reason = "RAG top1 分数不足"
                reason_code = "low_rag_score"
            elif margin is None or margin < self._calibration.margin_threshold:
                reason = "RAG 候选区分度不足"
                reason_code = "low_rag_margin"
            else:
                reason = None
            if reason is not None:
                return PolicyDecision(
                    result.route,
                    "suggest",
                    result.answer,
                    qa_code,
                    score,
                    margin,
                    reason,
                    reason_code,
                )
            if not allow_auto:
                return PolicyDecision(
                    result.route,
                    "suggest",
                    result.answer,
                    qa_code,
                    score,
                    margin,
                    "自动回复未启用",
                )
            if not bool(metadata.get("auto_reply_eligible", False)):
                return PolicyDecision(
                    result.route,
                    "suggest",
                    result.answer,
                    qa_code,
                    score,
                    margin,
                    "知识未获自动发送资格",
                    "auto_reply_ineligible",
                )
            return PolicyDecision(
                result.route, "auto_send", standard_answer, qa_code, score, margin, None
            )

        if not allow_auto:
            reason = "自动回复未启用"
        return PolicyDecision(
            result.route, "suggest", result.answer, qa_code, score, margin, reason
        )

    def _risk_reason(
        self, query: str, metadata: dict[str, object], conversation: dict[str, Any]
    ) -> str | None:
        if SENSITIVE_PATTERN.search(query):
            return "敏感或订单售后问题需人工处理"
        risk = str(metadata.get("risk_level") or "").lower()
        if risk in {"high", "高"} or bool(metadata.get("need_human")):
            return "知识库标记为高风险或必须转人工"
        if risk not in {"low", "medium", "低", "中"}:
            return "知识库风险等级不明确"
        category = str(metadata.get("category") or "")
        stage = str(metadata.get("service_stage") or "").lower()
        if category in {"伤病/恢复", "物流/售后"} or stage in {"post_sale", "售后"}:
            return "该问题类别不允许自动发送"
        product_code = str(metadata.get("product_id") or "")
        if product_code and product_code not in {"通用", "common"}:
            goods_name = _normalize(str(conversation.get("goods_name") or ""))
            product_name = _normalize(str(metadata.get("product_name") or ""))
            if not goods_name or not product_name or (
                product_name not in goods_name and goods_name not in product_name
            ):
                return "缺少匹配的商品卡片上下文"
        return None


def choose_calibration(
    records: list[dict[str, Any]], *, min_precision: float, min_samples: int
) -> Calibration:
    """从离线预测记录中选择覆盖量最大的合格分数/差值组合。"""
    usable = [
        row
        for row in records
        if row.get("top_score") is not None and row.get("margin") is not None
    ]
    if len(usable) < min_samples:
        return Calibration(sample_count=len(usable))
    scores = sorted({float(row["top_score"]) for row in usable})
    margins = sorted({float(row["margin"]) for row in usable})

    def grid(values: list[float]) -> list[float]:
        if len(values) <= 31:
            return values
        return sorted({values[round(i * (len(values) - 1) / 30)] for i in range(31)})

    best: Calibration | None = None
    for score_threshold in grid(scores):
        score_rows = [row for row in usable if float(row["top_score"]) >= score_threshold]
        for margin_threshold in grid(margins):
            accepted = [
                row for row in score_rows if float(row["margin"]) >= margin_threshold
            ]
            if not accepted:
                continue
            correct = sum(
                row.get("expected_qa") == row.get("predicted_qa") for row in accepted
            )
            precision = correct / len(accepted)
            lower_bound = wilson_lower_bound(correct, len(accepted))
            if lower_bound < min_precision:
                continue
            candidate = Calibration(
                enabled=True,
                sample_count=len(usable),
                accepted_count=len(accepted),
                precision=precision,
                precision_lower_bound=lower_bound,
                score_threshold=score_threshold,
                margin_threshold=margin_threshold,
            )
            if best is None or candidate.accepted_count > best.accepted_count:
                best = candidate
    return best or Calibration(sample_count=len(usable))
