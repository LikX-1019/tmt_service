"""Agent Graph 入口路由；只读取已恢复 State 的 next_node。"""

from __future__ import annotations

from app.agent.state import AgentState


GRAPH_ENTRY_NODES = frozenset(
    {
        "session_hydrate",
        "faq_exact",
        "guard",
        "social",
        "product_resolve",
        "product_load",
        "product_answer",
        "fallback",
        "response",
    }
)


def route_graph_entry(state: AgentState) -> str:
    """把新请求或显式恢复 State 路由到 allowlist 内的实际节点。"""
    if state.next_node in GRAPH_ENTRY_NODES:
        return state.next_node
    raise ValueError(f"未知或不容许恢复的 Graph entry node: {state.next_node}")


__all__ = ["GRAPH_ENTRY_NODES", "route_graph_entry"]
