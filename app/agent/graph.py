"""构建 Unified Chat Agent StateGraph。"""

from __future__ import annotations

from typing import Any
from dataclasses import replace

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.dependencies import AgentCapabilities, default_capabilities
from app.agent.coordinator import StateCoordinator
from app.agent.edges.entry_edge import route_graph_entry
from app.agent.constants import (
    FAQ_EXACT_NODE,
    FALLBACK_NODE,
    GUARD_NODE,
    HUMAN_TRANSFER_NODE,
    PRODUCT_ANSWER_NODE,
    PRODUCT_LOAD_NODE,
    PRODUCT_RESOLVE_NODE,
    RAG_CONTEXT_NODE,
    RESPONSE_NODE,
    SESSION_HYDRATE_NODE,
    SOCIAL_NODE,
)
from app.agent.edges.faq_edge import after_faq
from app.agent.edges.fallback_edge import after_fallback
from app.agent.edges.guard_edge import after_guard
from app.agent.edges.product_edge import after_product_load, after_product_resolve
from app.agent.edges.social_edge import after_social
from app.agent.nodes.faq import FAQNode
from app.agent.nodes.fallback import FallbackNode
from app.agent.nodes.guard import GuardNode
from app.agent.nodes.product import (
    ProductAnswerNode,
    ProductLoadNode,
    ProductResolveNode,
)
from app.agent.nodes.rag import RAGContextNode
from app.agent.nodes.human_transfer import HumanTransferNode
from app.agent.nodes.response import ResponseNode
from app.agent.nodes.session import SessionHydrateNode
from app.agent.nodes.social import SocialNode
from app.agent.protocols import (
    AgentGraphError,
    AgentGraphExecutionError,
    AgentNode,
    AgentNodeExecutionError,
)
from app.agent.state import AgentState, StatePatch


def _safe_error_code(exc: BaseException) -> str:
    """从异常类型生成可观测且不含动态细节的错误码。"""
    return type(exc).__name__.upper()


class GraphNodeAdapter:
    """LangGraph transport adapter：StatePatch 合并与节点生命周期。"""

    def __init__(
        self,
        node_name: str,
        node: AgentNode,
        *,
        coordinator: StateCoordinator | None = None,
        retryable: bool = True,
    ) -> None:
        self.node_name = node_name
        self._node = node
        self._coordinator = coordinator
        self._retryable = retryable

    async def __call__(self, raw_state: Any) -> dict[str, Any]:
        try:
            state = AgentState.model_validate(raw_state)
        except Exception as exc:
            raise AgentGraphExecutionError(
                "LangGraph 传入状态不是合法 AgentState"
            ) from exc
        working = state.model_copy(deep=True)
        try:
            if self._coordinator is not None:
                # Durable mode: StateCoordinator is the only lifecycle owner.
                working = await self._coordinator.run_node(
                    working,
                    self.node_name,
                    self._node,
                    retryable=self._retryable,
                )
                return working.model_dump()

            working.begin_node(self.node_name)
            patch = await self._node(working)
            if not isinstance(patch, StatePatch):
                raise TypeError("Agent Node 必须返回 StatePatch")
            working.apply_patch(patch)
            working.complete_node(self.node_name, next_node=patch.next_node)
            return working.model_dump()
        except Exception as exc:
            if self._coordinator is not None:
                # run_node has already saved failed:<node>; never fail it twice.
                raise AgentNodeExecutionError(
                    f"Agent Node {self.node_name} 执行失败",
                    failed_state=working,
                    node_name=self.node_name,
                    original_exception=exc,
                ) from exc
            working = _safe_failed_state(working, self.node_name, exc)
            raise AgentNodeExecutionError(
                f"Agent Node {self.node_name} 执行失败",
                failed_state=working,
                node_name=self.node_name,
                original_exception=exc,
            ) from exc


def _safe_failed_state(
    state: AgentState,
    node_name: str,
    exc: Exception,
) -> AgentState:
    """把异常转换成安全 failed state；生命周期错误不掩盖原始异常。"""
    try:
        if state.current_node == node_name:
            state.fail_node(
                node_name,
                code=_safe_error_code(exc),
                retryable=True,
                message=str(exc) or "Agent Node 执行失败",
            )
    except Exception:
        pass
    return state


def build_unified_chat_graph(
    capabilities: AgentCapabilities | None = None,
    coordinator: StateCoordinator | None = None,
) -> CompiledStateGraph:
    """构建当前 Unified Chat durable/resumable 业务主图。"""
    caps = capabilities or default_capabilities()
    builder: StateGraph = StateGraph(AgentState)
    nodes = {
        SESSION_HYDRATE_NODE: SessionHydrateNode(caps),
        FAQ_EXACT_NODE: FAQNode(caps),
        GUARD_NODE: GuardNode(caps),
        SOCIAL_NODE: SocialNode(caps),
        PRODUCT_RESOLVE_NODE: ProductResolveNode(caps),
        PRODUCT_LOAD_NODE: ProductLoadNode(caps),
        PRODUCT_ANSWER_NODE: ProductAnswerNode(caps),
        RAG_CONTEXT_NODE: RAGContextNode(caps),
        FALLBACK_NODE: FallbackNode(caps),
        HUMAN_TRANSFER_NODE: HumanTransferNode(),
        RESPONSE_NODE: ResponseNode(),
    }
    for name, node in nodes.items():
        builder.add_node(
            name,
            GraphNodeAdapter(name, node, coordinator=coordinator),
        )

    builder.add_conditional_edges(
        START,
        route_graph_entry,
        {name: name for name in nodes},
    )
    builder.add_edge("session_hydrate", "faq_exact")
    builder.add_conditional_edges(
        "faq_exact",
        after_faq,
        {RESPONSE_NODE: RESPONSE_NODE, GUARD_NODE: GUARD_NODE},
    )
    builder.add_conditional_edges(
        "guard",
        after_guard,
        {
            HUMAN_TRANSFER_NODE: HUMAN_TRANSFER_NODE,
            RESPONSE_NODE: RESPONSE_NODE,
            SOCIAL_NODE: SOCIAL_NODE,
        },
    )
    builder.add_conditional_edges(
        "social",
        after_social,
        {RESPONSE_NODE: RESPONSE_NODE, PRODUCT_RESOLVE_NODE: PRODUCT_RESOLVE_NODE},
    )
    builder.add_conditional_edges(
        "product_resolve",
        after_product_resolve,
        {
            RESPONSE_NODE: RESPONSE_NODE,
            PRODUCT_LOAD_NODE: PRODUCT_LOAD_NODE,
            RAG_CONTEXT_NODE: RAG_CONTEXT_NODE,
            FALLBACK_NODE: FALLBACK_NODE,
        },
    )
    builder.add_conditional_edges(
        "product_load",
        after_product_load,
        {
            PRODUCT_ANSWER_NODE: PRODUCT_ANSWER_NODE,
            RAG_CONTEXT_NODE: RAG_CONTEXT_NODE,
            FALLBACK_NODE: FALLBACK_NODE,
            RESPONSE_NODE: RESPONSE_NODE,
        },
    )
    builder.add_edge(RAG_CONTEXT_NODE, FALLBACK_NODE)
    builder.add_edge("product_answer", "response")
    builder.add_conditional_edges(
        "fallback",
        after_fallback,
        {HUMAN_TRANSFER_NODE: HUMAN_TRANSFER_NODE, RESPONSE_NODE: RESPONSE_NODE},
    )
    builder.add_edge("human_transfer", "response")
    builder.add_edge("response", END)
    try:
        # Durable checkpoints use the injected StateCoordinator, not LangGraph.
        return builder.compile()
    except Exception as exc:
        raise AgentGraphExecutionError("Agent StateGraph 编译失败") from exc


def build_agent_graph(rule_registry: Any | None = None) -> CompiledStateGraph:
    """兼容 G1 显式 Skeleton 构造入口。"""
    capabilities = default_capabilities()
    if rule_registry is not None:
        capabilities = replace(capabilities, rules=rule_registry)
    return build_unified_chat_graph(capabilities)


__all__ = [
    "AgentGraphError",
    "AgentGraphExecutionError",
    "AgentNodeExecutionError",
    "GraphNodeAdapter",
    "build_agent_graph",
    "build_unified_chat_graph",
]
