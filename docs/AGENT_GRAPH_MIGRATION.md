# Agent Graph Migration Plan

## Migration Status

```text
PHASE = Phase 2 — Unified Agent Graph Migration
GRAPH_MIGRATION_FREEZE = active
G0 = COMPLETED
G1 = COMPLETED
G2 = COMPLETED
G2A = COMPLETED
G2B = COMPLETED
G3 = COMPLETED
G4 = COMPLETED
G5 = COMPLETED
G5A = COMPLETED
G5B = COMPLETED
G6 = COMPLETED
G7 = COMPLETED
PLAN_VERSION = 1
BASELINE_DATE = 2026-09-20
```

本文档是 Agent Graph 迁移的唯一执行计划。在 Migration Acceptance 通过并把状态
改为 `GRAPH_MIGRATION_FREEZE = completed` 之前，默认暂停所有非迁移必需的新业务
功能开发。任何开发任务开始前必须先检查本文件状态。

## Current State Inventory

当前真实运行边界如下，不得把 Planned 描述成 Implemented：

```text
/api/v1/chat
    ↓
ChatService Facade
    ↓
AgentRuntime
    ↓
Unified Chat AgentGraph
    ↓
StateCoordinator
    ↓
FileCheckpointStore
```

```text
PDD BrowserMessage
    ↓
ConsoleRuntime._on_message()
    ↓
ConsoleRuntime._evaluate_batch()  [thin Graph invocation]
    ↓
PDD Channel Adapter → AgentRuntime → shared AgentGraph
    ↓
ConsoleRuntime channel policy / persistence / send queue
```

GitNexus G6 前调用关系与删除结果：

| Legacy orchestrator | Former upstream caller | Affected process | Pre-removal risk | G6 result |
|---|---|---|---|---|
| `ChatService._route_chat` | `ChatService.chat` | `chat`，8 traced process paths | LOW | Deleted |
| `ConsoleRuntime._evaluate_batch_legacy` | `ConsoleRuntime._evaluate_batch` → `ConsoleRuntime._evaluate_after_delay` → `ConsoleRuntime._on_message` | `_on_message`，7 traced process paths | LOW | Deleted |

G6 removed `_route_chat()`, `_evaluate_batch_legacy()`, `UNIFIED_CHAT_RUNTIME`,
and `PDD_AGENT_RUNTIME`. There is no configurable or hidden Legacy AI path.

已有迁移基础包括 `AgentState`、`ChatSessionState`、`ChatTurnState`、`StatePatch`、
`StateCoordinator`、`FileCheckpointStore`、`ChatStateRuntime` 和 PendingAction
安全语义。G1 已引入 LangGraph，`AgentRuntime` 和最小可执行 StateGraph 骨架，
G2B 已将生产 Unified Chat 切到 Unified Chat AgentGraph；G5B 已将 PDD 默认路径切到同一套
AgentRuntime/AgentGraph；G6 已删除两条 Legacy 回滚路径。

## Target Architecture

```text
Customer Demo ─┐
PDD ────────────┤
Future Taobao ──┤
Future Douyin ──┤
API ────────────┘
        ↓
   Channel Adapter
        ↓
    AgentRuntime
        ↓
     AgentGraph
        ↓
Node / Conditional Edge
        ↓
Service / Repository / Tool
        ↓
Database / LLM / External System
```

不同渠道可以保留不同 Adapter、Transport、AutoReplyPolicy、Sender 和 Connector，
但不得保留不同的客服业务决策核心。

## Non-negotiable Rules

1. Agent Graph 是客服业务 workflow 的唯一 orchestrator。
2. Workflow Step 使用 Node，Branch 使用 Conditional Edge；可复用能力继续留在
   Service、Repository 和 Tool。
3. Node 读取 `AgentState` 并返回 `StatePatch`，不得成为巨型 Service。
4. 禁止另建第二套 Graph State。
5. PostgreSQL 是商品事实 Source of Truth；MySQL、Milvus、Checkpoint 分别保存业务
   事实、向量知识和 Workflow Runtime。
6. 高风险 deterministic rules 必须成为前置 Guard。
7. 副作用 Tool 必须经过 PendingAction 和幂等检查点。
8. 迁移必须行为保持；先建立 characterization/regression baseline，再切换路径。
9. 不做一次性 Big Bang Rewrite。
10. 已完成 Graph 路径不得长期与旧路径并行提供同一业务能力。

## Migration Phases

每个阶段完成前必须在本文档更新状态、证据、测试结果和残留风险。禁止跳过
baseline，也禁止在同一个阶段混合多个不相关业务重写。

### Phase G0 — Current-state Inventory + Regression Baseline

| Item | Boundary |
|---|---|
| Status | `COMPLETED` |
| Goal | 固化 Unified Chat 与 PDD 的真实输入、输出、路由、状态和副作用行为。 |
| Scope | 只读取现有实现；新增 characterization/regression tests 和测试 fixture；可补充观测字段，但不改变业务决策。 |
| Prohibited | 重写业务规则；迁移 Graph；修改数据库契约；扩大旧 orchestrator。 |
| Tests | 新增 Unified Chat characterization tests；PDD multiroute/auto-reply tests；State/checkpoint contract tests。 |
| Acceptance | 商品绑定、URL、ID、名称匹配、候选、历史商品、语义指代、Greeting、Courtesy、Social、FAQ Exact、RAG、Fallback、Human Handoff、PDD Auto Reply、Checkpoint、Debug Fields 和 Logging Context 均有可重复断言。 |
| Rollback | 删除新增测试/fixture，不产生 Runtime 行为变化。 |

G0 基线结果见 `docs/AGENT_GRAPH_G0_BASELINE.md`。要点：

* Start commit：`378cfeb`；分支：`master`；working tree 开始时 clean。
* GitNexus index：4,724 nodes / 10,209 edges / 345 flows。
* Full regression：`uv run pytest -q` 通过，439 passed。
* 未新增业务代码或 characterization test；现有 439 个测试成为迁移回归入口。
* Unified Chat、PDD Runtime、capability、orchestration、state、persistence、side
  effect、Node/Edge mapping、coverage gap、migration risk 和 G1 scope 已盘点。
* `_evaluate_batch` 与 `_on_message` 的 downstream blast radius 分别为 CRITICAL
  和 CRITICAL；这是 ConsoleRuntime 内部共享耦合导致的结构风险。本次只修改文档，
  未改变业务执行流，无未解释运行时风险。

### Phase G1 — AgentRuntime + Executable Graph Skeleton

| Item | Boundary |
|---|---|
| Status | `COMPLETED` |
| Goal | 实现 AgentRuntime 和可执行 AgentGraph 骨架，打通 START → hydrate → guard → continue → response → END。 |
| Scope | `app/agent/`、Graph runtime wiring、最小 Node/Edge、编译/调用协议；用 feature flag 或测试入口验证，不切生产主路径。 |
| Prohibited | 把 Product/QA/PDD 全量逻辑搬进 Node；新增业务规则；替换生产 Unified Chat 或 PDD 路径。 |
| Tests | Graph 编译测试、Node 输入输出测试、Guard 前置顺序测试、StatePatch 契约测试、失败注入测试。 |
| Acceptance | 相同 `AgentState` 输入可执行主图；Node 返回 StatePatch；Guard 可终止或继续；异常可进入失败/恢复路径。 |
| Rollback | 移除 Runtime wiring/flag 后旧路径不受影响。 |

G1 已完成，运行契约和验收证据见 `docs/AGENT_GRAPH_G1_RUNTIME.md`。要点：

* 引入 `langgraph>=1.2,<2`，当前锁定 `1.2.11`；`uv sync --frozen` 通过。
* `StateGraph(AgentState)` 真实编译，`AgentRuntime.invoke()` 使用异步 `ainvoke`。
* Node contract 固定为 `AgentState → StatePatch`；`GraphNodeAdapter` 负责 deepcopy、
  lifecycle、`AgentState.apply_patch` 和 LangGraph transport snapshot。
* `START → session_hydrate → guard → response → END` 可执行；guard terminal 与
  continue 均覆盖。
* 节点失败产生内存中的 `FAILED` / `error.node` / `resume_from` / failed trace，
  且不污染输入 State。
* 未接入生产 DI，未配置 LangGraph checkpointer，未修改 ChatService / ConsoleRuntime。
* Agent tests：84 passed；full regression：453 passed；ruff、diff-check、
  `uv sync --frozen` 通过。

### Phase G2 — Unified Chat Migration (subphased)

| Item | Boundary |
|---|---|
| Status | `COMPLETED` |
| Goal | `/api/v1/chat` 默认从 `ChatService` Facade 进入 AgentRuntime，不再由 `_route_chat()` 决策。 |
| Scope | ChatService 请求适配、运行上下文创建、AgentRuntime invocation、ChatResponse 映射；迁移 Social、Intent、Product、FAQ、RAG、Fallback、Human、Response。 |
| Prohibited | 静默改变 FAQ exact 与 Guard 的先后顺序；改变 API contract；重写 QA/Product 内部算法；绕过 StatePatch。 |
| Tests | G0 characterization 全量对照；API contract tests；conversation product binding tests；failure/resume tests。 |
| Acceptance | 旧 orchestrator 输入 X 输出 Y，Graph 输入相同 X 输出等价 Y；ChatService 不再包含大规模客服流程判断。 |
| Rollback | 可通过明确开关恢复旧路径；开关删除前不得宣布该阶段完成。 |

G2 split into:

```text
G2A — Unified Chat Graph Parity
G2B — Unified Chat Production Cutover
```

`G2A` builds and tests the complete equivalent Unified Chat decision graph without
connecting production. `G2B` is the only phase allowed to make `ChatService`
facade invoke `AgentRuntime`, with an explicit short-lived rollback switch.

G2B cutover evidence lives in `docs/AGENT_GRAPH_G2B_CUTOVER.md`. Summary:

* Production default is `UNIFIED_CHAT_RUNTIME=graph`.
* `get_chat_service()` composes shared capabilities, the Unified Chat Graph, and
  AgentRuntime before injecting them into ChatService.
* Graph hydrates history and excludes the current customer turn from fallback history.
* Customer is persisted before execution; assistant only after success.
* The Graph-returned State is used for mapping, completion, logging, and checkpointing.
* Graph failures preserve the actual failed node in the coarse checkpoint.
* No automatic Graph-to-Legacy fallback exists.
* Validation: agent 110, chat service 30, chat API 10, full suite 479 passed.

G3 durable checkpoint evidence lives in `docs/AGENT_GRAPH_G3_CHECKPOINT.md`.
G4 hardening evidence lives in `docs/AGENT_GRAPH_G4_HARDENING.md`.
G5A PDD graph parity evidence lives in `docs/AGENT_GRAPH_G5A_PARITY.md`.

### Phase G3 — StatePatch + Node Checkpoint Integration

| Item | Boundary |
|---|---|
| Status | `COMPLETED` |
| Goal | 把粗粒度 `chat_turn` recorder 升级为 Node lifecycle checkpoint。 |
| Scope | Node before/after/failed/resume 保存点；StateCoordinator 与 Graph execution 对齐；副作用动作生命周期。 |
| Prohibited | 丢失 revision、retry、resume、PendingAction、UNCERTAIN 语义；把不可序列化对象写入 State；重复副作用。 |
| Tests | checkpoint revision/conflict tests；failed node resume tests；PendingAction EXECUTING/UNCERTAIN tests；旧 checkpoint schema upgrade tests。 |
| Acceptance | 每个真实 Node 生命周期可恢复；失败节点不产生半完成业务状态；副作用不确定时转人工。 |
| Rollback | 保留旧 recorder 路径直到新 checkpoint 证明等价后切换。 |

G3 completed durable node lifecycle without LangGraph checkpointer or automatic
startup recovery. See `docs/AGENT_GRAPH_G3_CHECKPOINT.md`.

G4 completed Business Graph Hardening & Structural Convergence without changing API
or PDD behavior. RAG retrieval and generation are separate durable nodes, Human
Transfer is explicit, Product routing is typed, current-product clearing is explicit,
Guard is channel-neutral, and node names share one registry.

### Phase G4 — Product / Social / FAQ / RAG / Fallback Full Migration

| Item | Boundary |
|---|---|
| Status | `COMPLETED` |
| Goal | Unified Chat 的全部主要业务步骤由业务级 Graph Node 表达。 |
| Scope | Graph business hardening / residual decomposition; subgraph cleanup; remove transitional compatibility; prepare Shared Agent capabilities for PDD reuse. |
| Prohibited | 重复 G2 的 Unified Chat initial migration；把 ProductResolver、SemanticProductResolver、QAService、RuleRegistry、ContextualFallbackService 的实现复制进 Node；借机新增 Tool、Memory 或 Intent 业务。 |
| Tests | 每个业务分支有 before/after 等价回归；边界输入和错误分支有断言；可观测字段一致。 |
| Acceptance | 主要业务决策不再从 `_route_chat()` 发起；分支由 Conditional Edge 表达；能力层调用关系保持 `Node → Service → Repository/Tool`。 |
| Rollback | 按业务路径切换和回滚，避免一次性删除旧逻辑。 |

### Phase G5 — PDD ConsoleRuntime Migration

| Item | Boundary |
|---|---|
| Status | `COMPLETED` (`G5A = COMPLETED`, `G5B = COMPLETED`) |
| Goal | PDD AI 决策迁移到同一 AgentRuntime/AgentGraph，ConsoleRuntime 不再拥有独立客服推理链。 |
| Scope | BrowserMessage Channel Adapter、AgentResult 到 AutoReplyPolicy 的映射、OutboundJob、SendWorker、Connector 生命周期。 |
| Prohibited | 在 `_evaluate_batch()` 继续新增 handoff/greeting/social/FAQ/product/RAG/policy 分支；绕过 AutoReplyPolicy 直接发送；造成重复回复或人工态误发送。 |
| Tests | PDD multiroute characterization；greeting/social/FAQ/product/handoff behavior tests；outbound idempotency tests；connector failure tests。 |
| Acceptance | PDD 与 Customer Demo 共享 AgentRuntime；`_evaluate_batch()` 不承担 AI workflow；发送队列结果和渠道状态仍由 ConsoleRuntime 管理。 |
| Rollback | 可切回 PDD legacy adapter，但不得长期保留双推理链。 |

G5A established a PDD BrowserMessage/Conversation/Shop adapter, explicit PDD
invocation of the shared `AgentRuntime` and `AgentGraph`, typed PDD business nodes,
and a Graph-output-to-channel-policy mapper. Legacy-vs-Graph parity tests cover
greeting, social, complaint/refund handoff, current explicit-human behavior, FAQ,
product answers and product failures, RAG, insufficient knowledge, and unavailable
knowledge. `AutoReplyPolicy`, persistence, outbound jobs, the send worker, connector
status, and human-takeover persistence remain outside the Graph.

G5B completed the controlled production adapter cutover. `ShopRuntimeManager` composes
one durable PDD `AgentRuntime`/shared business Graph and explicitly injects it into
each `ConsoleRuntime`. At G5B the default was `PDD_AGENT_RUNTIME=graph` with an
explicit emergency rollback; G6 subsequently removed that switch. Graph exceptions
become a suggest-only decision and never invoke the Legacy AI path. See
`docs/AGENT_GRAPH_G5B_CUTOVER.md` for the implementation and validation evidence.

### Phase G6 — Remove Duplicate Orchestration

| Item | Boundary |
|---|---|
| Status | `COMPLETED` |
| Goal | 删除或降级重复业务编排，消除 Graph 与 Legacy 双路径。 |
| Scope | `_route_chat()` 和 `_evaluate_batch()` 的业务分支、已废弃 wrapper、无用 routing helper、重复 decision mapping。 |
| Prohibited | 删除回归基线；保留可独立运行的隐藏 legacy path；改变已验收外部行为。 |
| Tests | 删除后执行 Unified Chat、PDD、State、API、rules、QA 回归；检查确认没有第二条业务决策入口。 |
| Acceptance | ChatService/ConsoleRuntime 只保留薄适配层或渠道职责，Graph 是唯一业务决策入口。 |
| Rollback | 通过 Git revert 恢复，不保留长期 switch。 |

G6 legacy removal evidence lives in `docs/AGENT_GRAPH_G6_LEGACY_REMOVAL.md`. Summary:

* Deleted `ChatService._route_chat()` and `ConsoleRuntime._evaluate_batch_legacy()`.
* Deleted `UNIFIED_CHAT_RUNTIME` and `PDD_AGENT_RUNTIME`.
* Unified Chat and PDD now have only the `AgentRuntime → AgentGraph` business path.
* SendWorker, `_send_job`, AutoReplyPolicy, persistence, and outbound responsibilities
  remain outside the Graph.
* Historical `chat_turn` checkpoints remain readable and safely resolve to manual state.
* Rollback is now a Git revert rather than a runtime switch.

### Phase G7 — Architecture Cleanup + Full Regression

| Item | Boundary |
|---|---|
| Status | `NOT_STARTED` |
| Goal | 清理命名、注释、死代码、配置和文档，使实现与目标架构一致。 |
| Scope | Agent 目录结构、Runtime 接口、Graph 文档、logging/debug contract、repository/service boundary；可运行完整测试。 |
| Prohibited | 引入新业务能力；重构 LLM 策略；改变状态契约。 |
| Tests | `uv run pytest -q`、`uv run ruff check .`、GitNexus change analysis、关键 integration/E2E。 |
| Acceptance | 无未解释 HIGH/CRITICAL 风险；文档、类型、注释与运行时一致；完整测试通过。 |
| Rollback | 清理项按独立 commit 回滚，不影响已迁移主链路。 |

G7 cleanup evidence lives in `docs/AGENT_GRAPH_G7_CLEANUP.md`. Summary:

* Removed unused Legacy state recorder methods and duplicate state mapping helpers.
* Removed the empty `ChatStateRuntime.start()` lifecycle and unused coordinator parameter.
* Made `AgentRuntime` require an explicitly compiled shared AgentGraph.
* Removed the unused ConsoleRuntime QA provider dependency.
* Renamed remaining transition identifiers without changing runtime behavior.
* Synchronized current architecture documentation.
* Preserved SendWorker, `_send_job`, AutoReplyPolicy, persistence, outbound, and checkpoint semantics.

### Phase G8 — Migration Acceptance

| Item | Boundary |
|---|---|
| Status | `NOT_STARTED` |
| Goal | 验收全量迁移并解除 Freeze。 |
| Scope | 只更新验收证据和 `GRAPH_MIGRATION_FREEZE` 状态；如发现缺陷先回到对应阶段修复。 |
| Prohibited | 在证据不完整时宣布完成；用部分路径通过代替全量验收；忽略外部环境导致的 skipped tests。 |
| Tests | 全量 pytest/ruff；Unified Chat 与 PDD 双通道 acceptance；checkpoint recovery；security/data-safety review；GitNexus clean change analysis。 |
| Acceptance | `AGENT_GRAPH_MIGRATION.md`、`STATE_DESIGN.md`、`RULE_ENGINE.md`、AGENTS 规则与实现一致；本文件“Migration Acceptance Checklist”全部通过。 |
| Rollback | Freeze 保持 active，并记录阻断项。 |

## Behavior-preserving Evidence

每个切流阶段必须记录：

```text
Before:
Legacy orchestrator input X → output Y

After:
AgentGraph input X → equivalent output Y
```

至少覆盖：

* 商品绑定、URL、ID、名称匹配、候选、历史商品、语义商品指代。
* Greeting、Courtesy、Social、FAQ Exact、RAG、Fallback。
* Human Handoff、PDD Auto Reply。
* Checkpoint、Debug Fields、Logging Context。

外部依赖不可用时必须明确标记 `SKIPPED` 和原因，不得写成通过。

## Tool And Memory Extension Rules

Tool 尚未完整实现，不阻碍 Graph 迁移。未来 Tool workflow 必须进入：

```text
ToolPlanNode
      ↓
SlotCheckNode
      ↓
Conditional Edge
      ├── Clarify
      ├── Confirmation
      └── Execute
              ↓
        ToolExecuteNode
```

底层 OrderQueryTool、LogisticsTool、RefundStatusTool、InventoryTool 等仍是独立能力。
HTTP、SQL 和外部 API 调用不得写进 Graph Edge；存在副作用时必须使用 PendingAction。

Memory 迁移初期允许 `MemoryNode = disabled / no-op`，但必须保留扩展位置。未来由
ShortTermMemoryNode、LongTermMemoryNode 或 ConversationSummaryNode 加载受控 Memory；
Memory Store 是能力层，不是 Graph 本身。

## Migration Acceptance Checklist

解除 Freeze 前必须逐项勾选并附证据：

- [ ] `/api/v1/chat` → AgentRuntime → AgentGraph。
- [x] PDD → Channel Adapter → AgentRuntime → AgentGraph。
- [ ] `ChatService` 不再承担业务 workflow。
- [ ] `ConsoleRuntime` 不再承担 AI workflow。
- [x] Guard、Social、Product、FAQ、RAG、Fallback、Human、Response 主要业务步骤由 Graph Node 表达。
- [ ] 主要业务分支由 Conditional Edge 表达。
- [x] StatePatch 是 Node 状态更新机制。
- [x] Checkpoint 与 Node execution 对齐。
- [x] Customer Demo 与 PDD 共享相同 Agent Runtime/AgentGraph contract。
- [ ] 旧 orchestrator 已删除或降级为薄 adapter。
- [ ] 完整测试通过；skipped 项均有外部原因。
- [ ] Ruff 通过。
- [ ] GitNexus change analysis 无未解释 HIGH/CRITICAL 风险。
- [ ] 架构文档与实际实现一致。

全部通过后才能修改为：

```text
GRAPH_MIGRATION_FREEZE = completed
```
