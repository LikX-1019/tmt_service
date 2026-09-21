"""Agent Graph 的节点名与 durable resume allowlist 唯一来源。"""

from __future__ import annotations

SESSION_HYDRATE_NODE = "session_hydrate"
FAQ_EXACT_NODE = "faq_exact"
GUARD_NODE = "guard"
SOCIAL_NODE = "social"
PRODUCT_RESOLVE_NODE = "product_resolve"
PRODUCT_LOAD_NODE = "product_load"
PRODUCT_ANSWER_NODE = "product_answer"
RAG_CONTEXT_NODE = "rag_context"
FALLBACK_NODE = "fallback"
HUMAN_TRANSFER_NODE = "human_transfer"
RESPONSE_NODE = "response"

RESUMABLE_GRAPH_NODES = frozenset(
    {
        SESSION_HYDRATE_NODE,
        FAQ_EXACT_NODE,
        GUARD_NODE,
        SOCIAL_NODE,
        PRODUCT_RESOLVE_NODE,
        PRODUCT_LOAD_NODE,
        PRODUCT_ANSWER_NODE,
        RAG_CONTEXT_NODE,
        FALLBACK_NODE,
        HUMAN_TRANSFER_NODE,
        RESPONSE_NODE,
    }
)

__all__ = [
    "FAQ_EXACT_NODE",
    "FALLBACK_NODE",
    "GUARD_NODE",
    "HUMAN_TRANSFER_NODE",
    "PRODUCT_ANSWER_NODE",
    "PRODUCT_LOAD_NODE",
    "PRODUCT_RESOLVE_NODE",
    "RAG_CONTEXT_NODE",
    "RESPONSE_NODE",
    "RESUMABLE_GRAPH_NODES",
    "SESSION_HYDRATE_NODE",
    "SOCIAL_NODE",
]
