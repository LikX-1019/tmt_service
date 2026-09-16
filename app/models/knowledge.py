"""正式 QA 知识表模型；审核、检索和自动发送资格彼此独立。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QAKnowledge(Base):
    """可版本化的标准 QA 条目。"""

    __tablename__ = "cs_qa"
    __table_args__ = (
        CheckConstraint("service_stage IN ('pre_sale', 'post_sale', 'general')", name="ck_cs_qa_service_stage"),
        CheckConstraint("risk_level IN ('low', 'medium', 'high')", name="ck_cs_qa_risk_level"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_cs_qa_status"),
        CheckConstraint("review_status IN ('usable', 'pending_review', 'pending_validation')", name="ck_cs_qa_review_status"),
        CheckConstraint("answer_version >= 1", name="ck_cs_qa_answer_version"),
        CheckConstraint("expired_at IS NULL OR effective_at IS NULL OR expired_at > effective_at", name="ck_cs_qa_effective_period"),
        Index("ix_cs_qa_product", "product_code"),
        Index("ix_cs_qa_intent", "intent_code"),
        Index("ix_cs_qa_type_status", "question_type", "status"),
        Index("ix_cs_qa_stage_status", "service_stage", "status"),
        Index("ix_cs_qa_review_status", "review_status"),
        Index("ix_cs_qa_effective", "effective_at", "expired_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    qa_code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    product_code: Mapped[str | None] = mapped_column(String(50))
    product_name: Mapped[str | None] = mapped_column(String(200))
    standard_question: Mapped[str] = mapped_column(String(500), nullable=False)
    standard_answer: Mapped[str] = mapped_column(Text, nullable=False)
    similar_questions: Mapped[list[str] | None] = mapped_column(JSON)
    keywords: Mapped[list[str] | None] = mapped_column(JSON)
    intent_code: Mapped[str | None] = mapped_column(String(100))
    question_type: Mapped[str | None] = mapped_column(String(50))
    service_stage: Mapped[str] = mapped_column(String(20), default="general", nullable=False)
    required_points: Mapped[list[str] | None] = mapped_column(JSON)
    prohibited_expressions: Mapped[list[str] | None] = mapped_column(JSON)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", nullable=False)
    retrieval_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_reply_eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    human_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    applicable_version: Mapped[str | None] = mapped_column(String(100))
    answer_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    review_status: Mapped[str] = mapped_column(String(30), default="pending_validation", nullable=False)
    source: Mapped[str | None] = mapped_column(String(255))
    import_batch_no: Mapped[str | None] = mapped_column(String(64))
    source_row_no: Mapped[int | None] = mapped_column(Integer)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
