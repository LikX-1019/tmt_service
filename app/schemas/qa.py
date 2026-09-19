"""QA API 的请求与响应模型。"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.chat import ChatProductCandidateView, ChatProductView


NonBlankQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]


class QARequest(BaseModel):
    query: NonBlankQuery
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    product_code: str | None = Field(default=None, max_length=64)
    product_name: str | None = Field(default=None, max_length=500)
    service_stage: Literal["pre_sale", "post_sale", "general"] | None = None
    knowledge_version: str | None = Field(default=None, max_length=100)


class QASource(BaseModel):
    chunk_id: str
    title: str | None = None
    source: str | None = None
    score: float | None = None


class QAResponse(BaseModel):
    answer: str
    route: Literal[
        "faq",
        "rag",
        "fallback",
        "product",
        "product_selection",
        "product_not_found",
        "product_missing",
        "product_link_invalid",
        "product_link_unsupported",
        "greeting",
        "human",
        "small_talk",
        "empathy",
    ]
    sources: list[QASource] = Field(default_factory=list)
    confidence: float | None = None
    match_score: float | None = None
    auto_reply_confidence: float | None = None
    product: ChatProductView | None = None
    products: list[ChatProductCandidateView] = Field(default_factory=list)
    product_resolution: (
        Literal[
            "request",
            "url",
            "message_id",
            "name_exact",
            "name_unique_contains",
            "history",
            "name_candidates",
            "conversation",
            "none",
        ]
        | None
    ) = None


class QADemoSource(BaseModel):
    rank: int
    chunk_id: str
    question: str
    answer: str
    product_name: str | None = None
    source: str | None = None
    dense_score: float | None = None
    bm25_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None


class QADemoResponse(QAResponse):
    retrieval_counts: dict[str, int] = Field(default_factory=dict)
    recalled_pairs: list[QADemoSource] = Field(default_factory=list)
