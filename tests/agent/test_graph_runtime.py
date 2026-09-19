"""G1 AgentRuntime 与 LangGraph Skeleton 的行为契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph

from app.agent.chat_runtime import ChatStateRuntime
from app.agent.edges.guard_edge import after_guard
from app.agent.graph import GraphNodeAdapter, build_agent_graph
from app.agent.nodes.guard import GuardNode
from app.agent.nodes.response import ResponseNode
from app.agent.nodes.session import SessionHydrateNode
from app.agent.protocols import (
    AgentGraphExecutionError,
    AgentNode,
    AgentNodeExecutionError,
)
from app.agent.runtime import AgentRuntime
from app.agent.state import (
    AgentState,
    ChatSessionState,
    ChatTurnState,
    StatePatch,
    WorkflowStatus,
)


def _state(message: str) -> AgentState:
    """创建满足 Session/Turn 契约的最小输入状态。"""
    return ChatStateRuntime().create_state(
        message=message,
        conversation_id=f"conversation-{message}",
        customer_id="customer-1",
        service_stage="general",
    )


async def _hydrated_guard_state(message: str) -> AgentState:
    """测试辅助：执行 hydrate 与 guard，生成可路由状态。"""
    state = _state(message)
    await SessionHydrateNode()(state)
    guard_patch = await GuardNode()(state)
    state.apply_patch(guard_patch)
    return state


def test_agent_graph_compiles_with_required_topology() -> None:
    """Graph 必须由 LangGraph 编译，并包含 START、三个业务节点和 END。"""
    build_agent_graph()
    representation = build_agent_graph().get_graph()

    assert {"__start__", "session_hydrate", "guard", "response", "__end__"}.issubset(
        set(representation.nodes)
    )
    assert any(
        edge.source == "guard" and edge.conditional for edge in representation.edges
    )


@pytest.mark.asyncio
async def test_agent_runtime_invokes_graph_async_and_returns_agent_state() -> None:
    """默认 Runtime 必须使用 ainvoke，并重新验证嵌套 State 类型。"""
    runtime = AgentRuntime()
    state = _state("今天天气怎么样")

    result = await runtime.invoke(state)

    assert isinstance(result, AgentState)
    assert isinstance(result.session, ChatSessionState)
    assert isinstance(result.turn, ChatTurnState)
    assert result.status is WorkflowStatus.COMPLETED
    assert result.completed_nodes == ["session_hydrate", "guard", "response"]


@pytest.mark.asyncio
async def test_business_nodes_return_state_patch() -> None:
    """业务节点契约固定为 AgentState 输入、StatePatch 输出。"""
    state = _state("这个商品怎么使用")

    hydrate_patch = await SessionHydrateNode()(state)
    guard_patch = await GuardNode()(state)
    response_patch = await ResponseNode()(state)

    assert isinstance(hydrate_patch, StatePatch)
    assert isinstance(guard_patch, StatePatch)
    assert isinstance(response_patch, StatePatch)
    assert hydrate_patch.next_node == "guard"
    assert response_patch.next_node is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_route"),
    [("我要人工", "human"), ("我要退款", "human")],
)
async def test_guard_terminal_routes_keep_fixed_reply_and_risk(
    message: str,
    expected_route: str,
) -> None:
    """高风险 terminal 输入必须复用当前规则结果并进入安全状态。"""
    result = await AgentRuntime().invoke(_state(message))

    assert result.status is WorkflowStatus.COMPLETED
    assert result.route == expected_route
    assert result.final_answer
    assert result.risk_level == "high"
    assert result.session is not None and result.session.human.required is True
    assert result.context["guard_terminal"] is True


@pytest.mark.asyncio
async def test_guard_continue_reaches_skeleton_response() -> None:
    """无规则命中的普通消息应走 continue 边并到达 Graph convergence。"""
    result = await AgentRuntime().invoke(_state("今天天气怎么样"))

    assert result.context["guard_terminal"] is False
    assert result.status is WorkflowStatus.COMPLETED
    assert result.final_answer is not None
    assert result.completed_nodes[-1] == "response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["我要人工", "今天天气怎么样"],
)
async def test_after_guard_returns_registered_route_keys(message: str) -> None:
    """条件边是纯函数，terminal 和 continue 都必须已注册到 response。"""
    state = await _hydrated_guard_state(message)
    expected = "terminal" if message == "我要人工" else "continue"

    assert after_guard(state) == expected


@pytest.mark.asyncio
async def test_state_patch_merge_uses_central_adapter() -> None:
    """Node patch 必须经 AgentState.apply_patch 合并，而不是另写 Graph merge。"""
    state = _state("今天天气怎么样")
    original_query = state.turn.original_query

    state.apply_patch(
        StatePatch(
            context={"graph_merge_checked": True},
            intent="graph_contract",
            next_node="guard",
        )
    )

    assert state.context["graph_merge_checked"] is True
    assert state.intent == "graph_contract"
    assert state.next_node == "guard"
    assert state.turn.original_query == original_query


@pytest.mark.asyncio
async def test_graph_does_not_mutate_original_input() -> None:
    """Graph 输入必须是隔离快照，成功执行不污染调用方对象。"""
    runtime = AgentRuntime()
    state = _state("我要退款")
    original = state.model_copy(deep=True)

    await runtime.invoke(state)

    assert state == original


@pytest.mark.asyncio
async def test_node_failure_has_safe_state_and_does_not_mutate_input() -> None:
    """节点异常必须保留 FAILED、trace 和 resume_from，并重新抛出异常。"""
    builder: StateGraph = StateGraph(AgentState)

    async def fail_node(state: AgentState) -> StatePatch:
        """测试专用失败节点。"""
        raise RuntimeError("boom")

    builder.add_node("boom", GraphNodeAdapter("boom", fail_node))
    builder.add_edge(START, "boom")
    builder.add_edge("boom", END)
    runtime = AgentRuntime(graph=builder.compile())
    state = _state("今天天气怎么样")
    original = state.model_copy(deep=True)

    with pytest.raises(AgentNodeExecutionError) as exc_info:
        await runtime.invoke(state)

    failed = exc_info.value.failed_state
    assert failed is not None
    assert failed.status is WorkflowStatus.FAILED
    assert failed.error is not None and failed.error.node == "boom"
    assert failed.error.code == "RUNTIMEERROR"
    assert failed.resume_from == "boom"
    assert failed.node_trace[-1].status.value == "failed"
    assert exc_info.value.original_exception is not None
    assert state == original


@pytest.mark.asyncio
async def test_runtime_revalidates_invalid_graph_output() -> None:
    """Graph 输出非法时必须显式失败，不能伪装成 AgentState。"""

    class InvalidGraph:
        async def ainvoke(self, state: AgentState) -> dict[str, object]:
            return {"thread_id": None}

    runtime = AgentRuntime(graph=InvalidGraph())  # type: ignore[arg-type]

    with pytest.raises(AgentGraphExecutionError):
        await runtime.invoke(_state("今天天气怎么样"))


def test_runtime_satisfies_agent_node_contract() -> None:
    """Node Protocol 是 runtime-checkable，三个 Skeleton 节点都符合契约。"""
    assert isinstance(SessionHydrateNode(), AgentNode)
    assert isinstance(GuardNode(), AgentNode)
    assert isinstance(ResponseNode(), AgentNode)


def test_production_paths_do_not_reference_graph_runtime() -> None:
    """静态检查生产装配与两个 Legacy orchestrator 未引用 G1 Runtime。"""
    production_files = [
        Path("app/api/dependencies.py"),
        Path("app/services/chat_service.py"),
        Path("app/services/console_runtime.py"),
    ]
    forbidden = ("AgentRuntime", "build_agent_graph")

    for path in production_files:
        source = path.read_text(encoding="utf-8")
        for symbol in forbidden:
            assert symbol not in source
