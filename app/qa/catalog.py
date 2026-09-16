"""从现有 MySQL QA 表或清洗工作簿构建统一目录。"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.config import Settings
from app.database.session import get_session_factory
from app.qa.models import FAQItem, RetrievalDocument


_VARIANT_SEPARATOR = re.compile(r"[\r\n；;/]+")
_QUESTION_CLAUSE = re.compile(r"[^？?]+[？?]?")


def _boolean_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "是", "需要"}


@dataclass(slots=True)
class QACatalog:
    faq_items: list[FAQItem]
    documents: list[RetrievalDocument]


def _json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        decoded = [value]
    return [str(item).strip() for item in decoded if str(item).strip()]


def expand_variants(values: Iterable[str]) -> list[str]:
    """拆分数据表中明确列出的复合问法，仍只做精确匹配。"""
    variants: list[str] = []
    seen: set[str] = set()
    for value in values:
        for separated in _VARIANT_SEPARATOR.split(value):
            separated = separated.strip()
            if not separated:
                continue
            clauses = [m.group(0).strip(" ？?") for m in _QUESTION_CLAUSE.finditer(separated)]
            candidates = [separated, *clauses] if len(clauses) > 1 else [separated]
            for candidate in candidates:
                if len(candidate) >= 4 and candidate not in seen:
                    seen.add(candidate)
                    variants.append(candidate)
    return variants


def _build_catalog(rows: Iterable[dict[str, Any]]) -> QACatalog:
    faq_items: list[FAQItem] = []
    documents: list[RetrievalDocument] = []
    for row in rows:
        qa_code = str(row["qa_code"])
        question = str(row["standard_question"]).strip()
        answer = str(row["standard_answer"]).strip()
        aliases = expand_variants([question, *_json_list(row.get("similar_questions"))])
        aliases = [item for item in aliases if item != question]
        source = str(row.get("source") or "cs_qa")
        metadata = {
            "question": question,
            "answer": answer,
            "product_id": row.get("product_code"),
            "product_name": row.get("product_name"),
            "category": row.get("question_type"),
            "doc_type": "qa",
            "service_stage": row.get("service_stage"),
            "risk_level": row.get("risk_level"),
            "applicable_version": row.get("applicable_version"),
            "required_points": _json_list(row.get("required_points")),
            "prohibited_expressions": _json_list(row.get("prohibited_expressions")),
            "retrieval_enabled": bool(row.get("retrieval_enabled")),
            "auto_reply_eligible": bool(row.get("auto_reply_eligible")),
            "human_required": bool(row.get("human_required")),
            "need_human": bool(row.get("human_required")),
            "status": row.get("status") or "published",
            "review_status": row.get("review_status") or "usable",
            "active": True,
            "source": source,
        }
        faq_items.append(
            FAQItem(
                id=qa_code,
                question=question,
                answer=answer,
                aliases=aliases,
                category=row.get("question_type"),
                tags=_json_list(row.get("keywords")),
                metadata=metadata,
            )
        )
        product = row.get("product_name") or "通用客服问题"
        documents.append(
            RetrievalDocument(
                chunk_id=qa_code,
                title=question,
                source=source,
                content=f"商品：{product}\n问题：{question}\n回答：{answer}",
                metadata=metadata,
            )
        )
    return QACatalog(faq_items=faq_items, documents=documents)


async def load_mysql_catalog() -> QACatalog:
    statement = text(
        """
        SELECT qa_code, product_code, product_name, standard_question,
               standard_answer, similar_questions, keywords, question_type,
               service_stage, risk_level, required_points,
               prohibited_expressions, retrieval_enabled,
               auto_reply_eligible, human_required, applicable_version,
               status, review_status, source
        FROM cs_qa
        WHERE status = 'published'
          AND retrieval_enabled = 1
          AND (effective_at IS NULL OR effective_at <= NOW())
          AND (expired_at IS NULL OR expired_at > NOW())
        ORDER BY priority DESC, qa_code ASC
        """
    )
    async with get_session_factory()() as session:
        result = await session.execute(statement)
        rows = [dict(row) for row in result.mappings().all()]
    if not rows:
        raise RuntimeError("cs_qa 中没有 published + usable 的有效 QA 数据")
    return _build_catalog(rows)


def _load_excel_catalog_sync(path: Path, sheet_name: str) -> QACatalog:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in workbook.sheetnames:
        raise ValueError(f"工作簿缺少工作表：{sheet_name}")
    sheet = workbook[sheet_name]
    iterator = sheet.iter_rows(min_row=5, values_only=True)
    headers = list(next(iterator))
    rows: list[dict[str, Any]] = []
    for values in iterator:
        row = dict(zip(headers, values))
        if (
            not row.get("问题编号")
            or row.get("QA建议状态") != "可用"
            or row.get("问题状态") not in {None, "有效"}
            or row.get("话术状态") not in {None, "当前有效"}
        ):
            continue
        rows.append(
            {
                "qa_code": row["问题编号"],
                "product_code": row.get("商品编码"),
                "product_name": row.get("商品名称"),
                "standard_question": row.get("标准问法"),
                "standard_answer": row.get("标准回答"),
                "similar_questions": json.dumps(
                    expand_variants([str(row.get("同义问法/触发表达") or "")]),
                    ensure_ascii=False,
                ),
                "keywords": json.dumps(
                    str(row.get("检索关键词") or "").split(), ensure_ascii=False
                ),
                "question_type": row.get("问题类型"),
                "service_stage": row.get("售前售后"),
                "risk_level": row.get("风险等级"),
                "required_points": json.dumps(
                    expand_variants([str(row.get("必答要点") or "")]),
                    ensure_ascii=False,
                ),
                "prohibited_expressions": json.dumps(
                    expand_variants([str(row.get("禁止表达") or "")]),
                    ensure_ascii=False,
                ),
                "retrieval_enabled": True,
                "auto_reply_eligible": False,
                "human_required": _boolean_value(row.get("是否必须转人工"))
                or _boolean_value(row.get("高风险问题")),
                "applicable_version": row.get("适用资料版本"),
                "status": "published",
                "review_status": "usable",
                "source": path.name,
            }
        )
    return _build_catalog(rows)


async def load_catalog(settings: Settings) -> QACatalog:
    source = settings.qa_data_source.strip().lower()
    if source == "mysql":
        return await load_mysql_catalog()
    if source == "excel":
        return await asyncio.to_thread(
            _load_excel_catalog_sync, settings.qa_excel_path, settings.qa_excel_sheet
        )
    raise ValueError(f"不支持的 QA_DATA_SOURCE：{settings.qa_data_source}")
