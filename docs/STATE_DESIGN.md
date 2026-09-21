# State Contract v2

本文定义客服 Agent 的可序列化状态契约。当前仓库已经实现 State Contract v2 的模型、
序列化、文件检查点、恢复协调和 PendingAction 安全语义，并已将最小 State Runtime
接入 `POST /api/v1/chat`。Unified Chat 的多轮商品事实仍以 `conversation_id` 和 MySQL
`chat_conversation_products` 为持久化绑定来源；Checkpoint 只负责单次 Turn 恢复。

## 1. 核心原则

1. State 保存“引用和运行状态”，不是所有业务事实。
2. PostgreSQL 商品数据库是商品事实唯一 Source of Truth。
3. Session State 只保存 `current_product.product_id` / `sku_id` 等稳定引用。
4. 每次真正回答商品问题时，根据 `product_id` 读取最新资料。
5. 查询到的商品资料只作为当前 Turn 的 `ResolvedProductContext`。
6. LLM 不直接读取数据库模型；独立 Context Builder 决定进入 Prompt 的内容。
7. 完整聊天历史不无限塞进 State。
8. 长期记忆不无限复制进 State。
9. Tool 实例不进入 State，只能保存可序列化描述。
10. State 必须可 JSON serialize / deserialize、可 Checkpoint、可恢复。

## 2. 状态分层

```text
AgentState
├── session: ChatSessionState | None   # v2 组合区，跨 Turn
└── turn: ChatTurnState | None         # v2 组合区，单次请求
```

当前 `AgentState` 仍保留 v1 顶层字段，原因如下：

- `FileCheckpointStore` 以 `AgentState` 为检查点信封。
- `StateCoordinator` 直接调用 `begin_node()`、`complete_node()`、`fail_node()` 和外部动作方法。
- Console/PDD 恢复语义已依赖 revision、resume 和 `PendingAction`，不能一次性重写。
- 既有 v1 检查点读取时会升级为 schema v2，`session` / `turn` 可为空。

新代码应优先创建和读取 `AgentState.session` 与 `AgentState.turn`；旧字段仅作为兼容信封和 Coordinator 工作区。

## 3. Runtime Status

### Current Runtime

```text
/api/v1/chat
    ↓
ChatService
    ↓
AgentRuntime → Unified Chat AgentGraph
```

生产 `ChatService` 通过 AgentRuntime 进入 Unified Chat AgentGraph；
`ChatService._route_chat()` 仅保留为显式 `UNIFIED_CHAT_RUNTIME=legacy` 回滚路径。
PDD 默认同样通过 `ConsoleRuntime` 的 PDD Channel Adapter 进入共享 AgentRuntime/
AgentGraph；PDD 的 AutoReplyPolicy、持久化和发送仍由 ConsoleRuntime 管理。

`ChatStateRuntime` 是旁路 recorder 和恢复契约：它创建 AgentState、记录 Rule、
Product、QA、完成和失败状态，并执行粗粒度 `chat_turn` 检查点，但不决定业务路由。

### Target Runtime

```text
Channel / API
    ↓
AgentRuntime
    ↓
AgentGraph
    ↓
Node / Conditional Edge
    ↓
Service / Repository / Tool
```

Unified Chat 与 PDD 最终共享同一个 AgentRuntime 和 AgentGraph。Node 通过
`StatePatch` 更新 State，Checkpoint 与真实 Node 生命周期对齐。`ChatService` 最终
降级为 Facade，`ConsoleRuntime` 最终只保留渠道、Connector、发送队列和发送结果
职责。

### Migration Status

项目处于 **Phase 2 — Unified Agent Graph Migration**，
`GRAPH_MIGRATION_FREEZE = active`。Agent Graph 尚未完全接管；阶段计划、验收条件
和回滚边界以 `docs/AGENT_GRAPH_MIGRATION.md` 为准。
G2B 已完成 Unified Chat 生产切流：生产 `/api/v1/chat` 通过 ChatService Facade 进入
AgentRuntime 和 Unified Chat AgentGraph。`_route_chat()` 只保留为显式 `legacy`
回滚路径。G5B 已完成 PDD 默认 Graph 切流；PDD legacy 仅保留为显式回滚，G6
再删除重复编排。

### Implemented

- `ChatSessionState`、`ChatTurnState`、`AgentState`、`StatePatch` 和强类型子状态模型。
- `ProductReference`、`ProductResolutionState`、`ResolvedProductContext`、`RetrievalState`、`ToolState`、`HumanState`、`WorkflowState` 和 `PendingAction`。
- JSON serialization / deserialization，包含既有 v1 checkpoint shape 读取时升级为 schema v2。
- `FileCheckpointStore` 原子 JSON 检查点、revision 冲突检测和可恢复状态枚举。
- `StateCoordinator` 节点前后检查点、retryable failure resume、running node resume 和 pending action safety。
- `PendingAction` 在 `EXECUTING` 或 `UNCERTAIN` 恢复时转人工，避免重复发送、重复退款或重复取消订单。
- `/api/v1/chat` 每次请求创建独立 `AgentState`、`ChatSessionState` 和 `ChatTurnState`。
- `ChatStateRuntime` 记录 Rule、Product、QA、完成和失败状态，并执行粗粒度 `chat_turn` 检查点。
- MySQL conversation-product binding hydrate Session 商品引用；PostgreSQL 商品事实只进入当前 Turn。
- 成功 Turn 在 completed checkpoint 写入后清理文件；失败或中断 checkpoint 保留用于恢复。
- `AgentRuntime` 和 LangGraph StateGraph skeleton 可执行 `START → session_hydrate →
  guard → response → END`；Node 返回 `StatePatch` 并通过 `AgentState.apply_patch` 合并。
- `AgentReplyState`、有界历史/商品引用状态和 Unified Chat FAQ/Guard/Social/Product/
  Fallback Graph path 已实现，并通过 Legacy parity 测试。

### Runtime Boundary

- `ChatService` 生产路径只负责请求/响应适配和 Graph 生命周期；旧的
  `MySQL binding hydrate → QA exact → RuleRegistry → SocialRouter →
  ProductResolver/ProductRepository → fallback QA context` 顺序只在显式 legacy
  回滚中保留。
- `ChatStateRuntime` 是旁路运行记录和恢复契约，不参与业务决策，也不替代 MySQL 或
  PostgreSQL-backed product API。
- 每个 HTTP 请求创建新 `run_id` / `turn_id`；相同 `conversation_id` 复用 `session_id`，但不复用上一轮商品事实快照。
- 下一轮商品追问仍通过 MySQL binding 得到 `product_id`，并重新读取 PostgreSQL 商品资料。
- Unified Chat and PDD production use the shared AgentRuntime/AgentGraph contract;
  PDD ConsoleRuntime remains the channel policy, persistence, queue, and connector
  boundary.
- 每个 Unified Chat production Graph Node lifecycle 由 StateCoordinator durable
  checkpoint：before / after / failed。
- StateCoordinator 拥有 Node lifecycle；AgentRuntime 可从 actual next/failed node
  显式恢复，但没有 automatic startup recovery。
- Legacy rollback 仍保留粗粒度 `chat_turn` checkpoint。
- Explicit nodes: SessionHydrate、FAQ、Guard、Social、ProductResolve、ProductLoad、
  ProductAnswer、RAGContext、Fallback、HumanTransfer、Response。
- Tools and Memory are not implemented; standalone IntentNode remains a future
  extension point.

### Planned For Graph Migration

- Chat Runtime Integration。
- Memory Store。
- Context Builder / `LLMContextBuilder`。
- Query Rewrite。
- Tool Runtime。
- Production Agent Graph takeover。

## 4. State Contract

### ChatSessionState

| 字段 | 用途 |
|---|---|
| `schema_version` | 固定为 `2` |
| `session_id` | 客服会话稳定 ID |
| `thread_id` | 渠道线程 / conversation 稳定 ID |
| `channel` | `customer_demo`、`pdd`、`taobao`、`douyin`、`other` |
| `tenant_id` | 租户隔离 |
| `shop_id` | 店铺隔离 |
| `customer_id` | 顾客 ID |
| `current_product` | 当前咨询商品引用，仅保存 `ProductReference` |
| `recent_product_ids` | 最近咨询商品 ID，用于切换和比较 |
| `service_stage` | 售前、售后或通用阶段 |
| `short_term_memory` | 最近消息 ID、摘要和最后消息 ID |
| `long_term_memory` | 本轮加载的长期记忆 ID 与片段 |
| `human` | 人工介入生命周期 |
| `workflow` | 会话关联的 Workflow 恢复信息 |
| `created_at` / `updated_at` | 会话状态时间 |

### ChatTurnState

| 字段 | 用途 |
|---|---|
| `schema_version` | 固定为 `2` |
| `turn_id` | 单次请求处理 ID |
| `session_id` | 所属会话 |
| `run_id` | 本次 Workflow run |
| `request_id` | API / 渠道请求 ID |
| `input_message_id` | 用户输入消息 ID |
| `output_message_id` | 最终助手消息 ID |
| `original_query` | 原始用户问题 |
| `rewritten_query` | 未来 Query Rewrite 结果；本轮只定义字段 |
| `rewrite_applied` | 是否实际应用改写 |
| `product` | 当前 Turn 商品解析状态 |
| `intent` / `route` | 意图与受控路由 |
| `retrieval` | RAG context、候选、计数与证据状态 |
| `tool` | Tool 描述、参数、追问和执行状态 |
| `human` | 当前 Turn 人工介入状态 |
| `draft_answer` / `final_answer` | 草稿和最终答案 |
| `fallback_reason` | 降级原因 |
| `risk_level` / `risk_reasons` | 风险等级与原因 |
| `workflow` | 当前 Turn 的节点、retry、revision 和副作用状态 |
| `started_at` / `completed_at` | Turn 时间 |

### StateMessage

| 字段 | 用途 |
|---|---|
| `id` | State 内消息 ID |
| `session_id` / `turn_id` | 关联会话和 Turn |
| `role` | system、customer、assistant、tool、human |
| `kind` | text、image、product、tool_result、system_event |
| `content` | 当前执行所需短内容；完整历史在 MySQL |
| `platform_message_id` | 渠道平台消息 ID |
| `reply_to_message_id` | 被回复消息 ID |
| `created_at` | 创建时间 |
| `metadata` | 非敏感扩展信息 |

### ProductReference

| 字段 | 用途 |
|---|---|
| `product_id` | 商品数据库稳定主键 |
| `sku_id` | 当前 SKU |
| `source` | 商品上下文来源 |
| `confidence` | 从消息识别时的置信度 |
| `selected_at` | 选择时间 |

禁止在 Session State 中保存 `name`、`selling_points`、`specifications`、`usage`、`warnings`、`after_sales_limits` 等完整商品事实。

### ProductResolutionState

| 字段 | 用途 |
|---|---|
| `reference` | 待解析商品引用 |
| `status` | `not_required`、`pending`、`resolved`、`not_found`、`failed` |
| `context` | 仅当前 Turn 的 `ResolvedProductContext` |
| `error_code` | `not_found` / `failed` 的安全错误码 |

### ResolvedProductContext

包含商品名、类目、摘要、卖点、规格、用法、适用人群、注意事项、售后限制、来源版本与解析时间。它是本轮执行快照，不是 Session 长期状态。

### ShortTermMemoryState

| 字段 | 用途 |
|---|---|
| `recent_message_ids` | 最近消息 ID，完整消息由 MessageRepository 加载 |
| `conversation_summary` | 会话摘要 |
| `last_user_message_id` | 最后用户消息 ID |
| `last_assistant_message_id` | 最后助手消息 ID |
| `max_recent_messages` | 加载窗口 |

### LongTermMemoryState

| 字段 | 用途 |
|---|---|
| `loaded_memory_ids` | 本轮加载的记忆 ID |
| `retrieved_memories` | 本轮实际进入运行状态的记忆片段 |
| `loaded_at` | 加载时间 |

### RetrievalContext

| 字段 | 用途 |
|---|---|
| `product_id` / `sku_id` | 稳定商品过滤 |
| `service_stage` | 服务阶段过滤 |
| `knowledge_version` | 知识版本过滤 |

商品名称不能作为主要过滤条件，只能用于展示或 fallback。

### RetrievalState

| 字段 | 用途 |
|---|---|
| `context` | 本次传给检索器的稳定上下文 |
| `candidates` | 候选证据 |
| `dense_count` / `bm25_count` / `fusion_count` / `rerank_count` | 各阶段数量 |
| `top_score` / `score_margin` | Top 分数与差值 |
| `evidence_sufficient` | 证据是否足以生成答案 |

### RetrievalEvidence

| 字段 | 用途 |
|---|---|
| `document_id` / `chunk_id` | 文档与块 ID |
| `content` | 进入决策的证据内容 |
| `source` | 来源 |
| `product_id` | 商品归属 |
| `dense_score` / `bm25_score` / `fusion_score` / `rerank_score` | 各阶段分数 |
| `score` | v1 兼容分数 |
| `metadata` | 审计和安全元数据 |

### ToolDescriptor / ToolState

`ToolDescriptor` 只保存名称、类目、启用状态、是否需确认和风险级别。`ToolState` 保存当前选中 Tool、参数、缺失参数、状态和结果引用。Tool 实例和执行依赖由 ToolRegistry 管理，Tool 大结果由独立 Result Store 管理。

### HumanState

| 字段 | 用途 |
|---|---|
| `required` | 是否需要人工 |
| `status` | none、requested、waiting、active、resolved |
| `reason_code` / `reason` | 原因 |
| `requested_at` | 请求时间 |
| `assigned_agent_id` | 处理人 |
| `resume_allowed` | 人工处理后是否允许自动恢复 |

### WorkflowState 与 PendingAction

`WorkflowState` 保存 run、节点、resume、retry、revision、checkpoint reason、错误和 pending action。`PendingAction` 保留 `prepared -> executing -> succeeded/failed/uncertain`，`executing` 崩溃后恢复时必须转为 `uncertain` 并人工核对，不能自动重试。

## 5. 商品上下文流

当前 `/api/v1/chat` 的第一阶段 State Runtime 路径是：

```text
POST /api/v1/chat
        ↓
Create AgentState / Session / Turn
        ↓
initial + before:chat_turn checkpoint
        ↓
ChatService + ChatStateRuntime recorder
        ↓
RuleRegistry
        ↓
ProductResolver
        ↓
conversation_id + chat_conversation_products
        ↓
product_id
        ↓
PostgreSQL product_api_profiles
        ↓
ProductAnswer
        ↓
completed checkpoint + cleanup
```

`chat_conversation_products` 是持久化 conversation-product binding；`ChatSessionState.current_product`
是 State Contract 中的运行时 `ProductReference`。两者都只应保存商品引用，不是商品事实的
Source of Truth。

后续完整 Agent Runtime 目标流程：

```text
User: “这个怎么使用？”
        ↓
session_id
        ↓
加载 ChatSessionState
        ↓
session.current_product.product_id
        ↓
ProductRepository / ProductProvider
        ↓
商品数据库
        ↓
ResolvedProductContext
        ↓
ChatTurnState.product.context
        ↓
加载 Short Memory
        ↓
加载 Long Memory
        ↓
RAG Retrieval
        ↓
LLMContextBuilder
        ↓
{
  current_product,
  recent_messages,
  conversation_summary,
  memories,
  retrieval_evidence
}
        ↓
LLM
        ↓
Final Answer
```

禁止让 LLM 根据 `product_id` 猜测商品资料；禁止把完整商品资料长期复制进 Session State。

## 6. State 与 Prompt 的边界

State 不是 Prompt。未来 `LLMContextBuilder` 输入 `ChatSessionState`、`ChatTurnState`、短期记忆、长期记忆和 RAG 证据，输出受控 `LLMContext`。哪些商品字段、多少历史消息、多少记忆和哪些证据进入 Prompt，由 Context Builder 决定，而不是把 State 原样发给模型。

## 7. Persistence Boundary

| 数据 | Source of Truth |
|---|---|
| 完整聊天历史 | MySQL `messages` |
| 客服会话和顾客关系 | MySQL `conversations` / `customers` |
| 当前统一 Chat 商品绑定 | MySQL `chat_conversation_products` |
| session_id | Session State / DB；当前 `/api/v1/chat` 使用 `conversation_id` |
| turn_id | Turn State / Checkpoint；当前 `/api/v1/chat` 每次请求生成新 ID |
| message_id | MySQL `messages` |
| 当前 product_id / sku_id | MySQL binding hydrate Session State `ProductReference` |
| 商品完整资料 | PostgreSQL `products` / `product_variants` / `product_api_profiles` |
| 当前商品快照 | Turn State |
| 最近消息 ID | ShortTermMemoryState |
| 会话摘要 | Session / Memory Store |
| 长期记忆 | Memory Store |
| QA 知识库 | MySQL `cs_qa` |
| QA vectors | Milvus |
| RAG candidates | TurnState / trace |
| Tool 实例 | ToolRegistry |
| Tool result 大对象 | Tool Result Store，State 只存 ref |
| Human 状态 | Session State |
| Checkpoint | Checkpoint Store |
| Workflow execution | TurnState / Checkpoint |

MySQL、PostgreSQL、Milvus 和 Checkpoint / State 的边界如下：

- MySQL 保存客服业务持久化：`conversations`、`messages`、`outbound_jobs`、`reply_decisions`、`connector_events`、`cs_qa` 和 `chat_conversation_products` 等。
- PostgreSQL 保存商品事实，`product_api_profiles` 是商品客服 API 读取的已发布商品资料视图。
- Milvus 保存 QA/RAG vectors，不保存商品主数据或长期业务状态。
- Checkpoint / State 保存 runtime、recovery 和 workflow state，不替代 MySQL/PostgreSQL 的 Source of Truth。

## 8. StatePatch 兼容策略

`StatePatch` 保留 v1 的：

- `messages`
- `context`
- `slots`
- `tool_results`
- `evidence`
- 顶层 intent / route / risk / answer / next_node

同时新增强类型分区：

- `current_product`
- `product`
- `retrieval`
- `tool`
- `human`
- `workflow`

旧字段按原 merge 语义处理；强类型分区只有在对应 `session` / `turn` 已挂载时才允许写入，避免静默丢失。
