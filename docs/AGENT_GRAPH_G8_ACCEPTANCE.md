# Agent Graph G8 Migration Acceptance

## Result

```text
G8 = COMPLETED
GRAPH_MIGRATION_FREEZE = completed
AgentGraph Migration = COMPLETED
```

本文件是 AgentGraph 迁移的最终验收记录。G8 不改动运行时行为、Graph topology、
State 契约、数据库契约、发送队列或渠道策略；它只固化验收证据并解除 Migration
Freeze。G0-G7 的历史证据文档保持原样。

## 1. Baseline

```text
Task start branch = master
Task start HEAD = 2c98dcf   (fix(agent): enforce guard before faq answers)
G7 cleanup commit = cdb61e0 (refactor(agent): complete graph architecture cleanup)
Working tree at start = clean
```

GitNexus index 在验收开始前落后于 HEAD 一个 commit（`lastCommit = cdb61e0`），
因此先运行：

```bash
node .gitnexus/run.cjs analyze --index-only
```

刷新后索引：

```text
5,344 nodes | 11,629 edges | 188 clusters | 344 flows
```

索引版本与验收时 HEAD 一致，后续 `context` / `query` / `detect-changes` 结果基于当前代码。

## 2. Final Architecture

唯一客服业务 orchestration path 为共享 AgentGraph。两个生产入口都只做渠道适配：

```text
POST /api/v1/chat
    ↓
app.api.v1.chat.chat
    ↓
ChatService.chat  [facade: request/response 适配 + 消息持久化]
    ↓
AgentRuntime.invoke
    ↓
build_unified_chat_graph() → shared AgentGraph
```

```text
BrowserMessage
    ↓
ConsoleRuntime._on_message
    ↓
ConsoleRuntime._evaluate_batch  [thin Graph invocation]
    ↓
invoke_pdd_agent → PDD Channel Adapter → AgentRuntime → shared AgentGraph
    ↓
AutoReplyPolicy / ReplyDecision persistence / KnowledgeGap / OutboundJob
    ↓
SendWorker → _send_job → connector.send_message
```

GitNexus `context` 证据：

| Symbol | Verified callers / callees |
| --- | --- |
| `ChatService.chat` | calls `AgentRuntime.invoke`、`agent_state_to_chat_response`、chat message repository |
| `invoke_pdd_agent` | called only by `ConsoleRuntime._evaluate_batch`; calls `AgentRuntime.invoke` |
| `build_unified_chat_graph` | 唯一 `StateGraph(AgentState)` 构建入口 |

两个生产组合点（`app/api/dependencies.py:get_chat_service`、
`app/services/shop_runtime_manager.py:_build_runtime`）注入的都是同一
`build_unified_chat_graph(capabilities, coordinator=...)`。

不存在第二条业务 orchestration path：

```text
rg 全仓库: 无 ChatService._route_chat / ConsoleRuntime._evaluate_batch_legacy
rg 全仓库: 无 UNIFIED_CHAT_RUNTIME / PDD_AGENT_RUNTIME runtime switch
rg app/:   仅一个 AgentRuntime、一个 AgentState family、一个 StateGraph builder
GitNexus check --cycles: no circular imports
```

`ChatService` 只负责请求/响应适配、消息持久化和 Graph 调用；`ConsoleRuntime`
只保留渠道策略、持久化、发送队列和 connector 生命周期。

### Graph nodes and conditional edges

Node 名唯一来源为 `app/agent/constants.py`，durable resume allowlist 复用同一集合：

```text
session_hydrate · pdd_handoff · pdd_greeting · pdd_intent · pdd_rag · faq_exact
guard · social · product_resolve · product_load · product_answer · rag_context
fallback · human_transfer · response
```

主要业务分支全部由 Conditional Edge 表达：

| After node | Conditional edge |
| --- | --- |
| `session_hydrate` | channel == `pdd` → `pdd_handoff`；否则 → `guard` |
| `guard` | human / terminal reply / PDD social / Unified `faq_exact` |
| `faq_exact` | response / PDD `pdd_intent` / Unified `social` |
| `social` | response / PDD `faq_exact` / Unified `product_resolve` |
| `product_resolve` | human / response / `product_load` / `rag_context` / `fallback` |
| `product_load` | human / `product_answer` / `rag_context` / `fallback` / response |
| `product_answer` | human / response |
| `fallback` | human / response |

Node 仍遵守 `AgentState → StatePatch` 契约（`GraphNodeAdapter` 强制校验并深拷贝输入）。
`RuleRegistry`、`QAService`、`ProductResolver`、`SemanticProductResolver`、
`ContextualFallbackService`、`AutoReplyPolicy`、Repository 与 Tool 仍是能力层，
没有被复制进 Node。

## 3. Dual-Channel Acceptance

### Unified Chat

```text
/api/v1/chat → ChatService facade → AgentRuntime → shared AgentGraph
```

实测节点序列（`tests/agent/test_unified_chat_graph_parity.py`）：

```text
FAQ 命中:   session_hydrate → guard → faq_exact → response
高风险终态: session_hydrate → guard → human_transfer → response
```

### PDD

```text
BrowserMessage → ConsoleRuntime → PDD Channel Adapter → AgentRuntime
              → shared AgentGraph → AutoReplyPolicy / persistence / outbound
              → SendWorker → Connector
```

PDD topology 在验收过程中未发生变化：

```text
session_hydrate → pdd_handoff → pdd_greeting → social → faq_exact
               → pdd_intent → product_resolve / pdd_rag → response
```

验收未修改任何运行时代码，因此 PDD parity 结果与 G5A/G5B/G6/G7 结论一致，并由回归测试复核。

## 4. Guard / Safety Acceptance

Unified Chat 实际顺序：

```text
session_hydrate → guard → faq_exact → social / product / RAG / fallback
```

高风险 terminal Guard 在 FAQ、Product、RAG、通用 LLM 之前执行，且不可被 FAQ exact 覆盖：

* `HumanHandoffRule`（priority 100）、`ComplaintRule`（95）、`AfterSaleRiskRule`（90）
  均为 terminal 规则，命中即路由到 `human_transfer`。
* `test_guard_prevents_faq_exact_override_for_high_risk`：输入 `我要退款` 且 FAQ 索引中
  存在精确 refund 答案时，实际 `completed_nodes = session_hydrate, guard, human_transfer,
  response`；`faq_exact` 未执行，QA 未被调用。
* `test_faq_exact_hit_runs_after_guard`：普通 FAQ 顺序为 `session_hydrate, guard,
  faq_exact, response`，`faq_hit=True`。

行为覆盖（均有可重复断言）：

| Behavior | Evidence |
| --- | --- |
| Human handoff | `tests/rules/test_human_handoff.py`, `test_rule_paths_have_legacy_parity[我要人工]` |
| Complaint | `tests/rules/test_complaint.py`, PDD `pdd_handoff` parity |
| Refund / after-sale risk | `tests/rules/test_after_sale_risk.py`, `test_guard_prevents_faq_exact_override_for_high_risk` |
| FAQ exact + 高风险 | `test_guard_prevents_faq_exact_override_for_high_risk` |
| 普通 FAQ 正常 | `test_faq_exact_hit_runs_after_guard`, `test_exact_faq_prevents_product_and_rag_paths` |
| 政策类退款问题不误终止 | `test_refund_policy_question_continues_to_fallback` |

PDD 侧高风险仍由 `pdd_handoff`（售后/争议 hard handoff）在 greeting、FAQ、intent 之前处理，
并由 `test_handoff_precedes_faq_and_keeps_conversation_manual` 及 G5A parity 覆盖。

## 5. State / Checkpoint / Resume Acceptance

唯一 Graph State Contract：

```text
AgentState / ChatSessionState / ChatTurnState / StatePatch
```

不存在第二套 State（`^class .*State` 只命中 `app/agent/state.py` 单一 family）。

Checkpoint 与真实 node lifecycle 对齐（`StateCoordinator.run_node`）：

```text
before:<node>   执行前保存（status=RUNNING）
after:<node>    仅在 apply_patch 成功后保存
failed:<node>   异常时保存，保留 error.node / resume_from / retryable
```

验收证据（`tests/agent/test_graph_checkpoint_resume.py`、`test_state_checkpoint.py`）：

| Requirement | Evidence |
| --- | --- |
| 每个成功 Node 都有 before/after | `test_successful_product_path_has_before_and_after_for_every_node` |
| 早退不 checkpoint 后续节点 | `test_faq_exact_early_return_does_not_checkpoint_later_nodes` |
| failed node 不产生 after | `test_failed_product_answer_persists_before_and_failed_without_after` |
| resume 只重跑失败节点 | `test_failed_product_answer_resume_retries_only_failed_node` |
| retry attempt 递增 | `test_interrupted_fallback_resume_starts_at_fallback_attempt_two` |
| 中断的 RUNNING node 从 resume_from 恢复 | `test_coordinator_recovers_interrupted_regular_node` |
| PendingAction EXECUTING/UNCERTAIN 转人工 | `test_pending_action_executing_or_uncertain_is_manual_and_not_resumed` |
| UNCERTAIN 不自动重试副作用 | `test_uncertain_external_action_never_auto_retries` |
| 自相矛盾的 SUCCEEDED 转人工 | `test_inconsistent_succeeded_pending_action_is_manual` |
| revision 冲突不被覆盖 | `test_checkpoint_conflict_is_not_overwritten` |
| historical `chat_turn` checkpoint 安全降级为人工 | `test_legacy_chat_turn_checkpoint_is_manual_not_reexecuted` |
| checkpoint JSON round-trip | `test_after_checkpoint_state_round_trips_json` |
| Node 结果与 Conditional Edge 一致 | `test_faq_next_node_matches_conditional_edge`, `test_guard_next_node_matches_conditional_edge`, `test_social_next_node_matches_conditional_edge`, `test_product_load_next_node_matches_conditional_edge` |

成功 Turn 在写入 completed checkpoint 后清理文件；失败或中断 checkpoint 保留用于显式 resume。
没有 automatic startup recovery；`AgentRuntime.resume(run_id)` 只处理显式指定的 run。

## 6. Channel / Side-effect Boundary

以下职责仍在 Graph 之外，未出现在 `app/agent/` 的 node 调用链中：

```text
AutoReplyPolicy            → app/services/auto_reply_policy.py
ReplyDecision persistence  → ConsoleRepository.add_decision
KnowledgeGap persistence   → ConsoleRepository knowledge gaps
human takeover persistence → ConsoleRuntime._handoff
OutboundJob                → ConsoleRepository.create_outbound_job
SendWorker / _send_job     → ConsoleRuntime
connector.send_message     → PddPlaywrightConnector
SendUncertain handling     → ConsoleRuntime._send_job + _pause_after_send_failure
ShopRuntimeManager         → connector / runtime lifecycle
```

`app/agent/channels/pdd_adapter.py` 是 Channel Adapter：它把 Graph 输出的渠道无关决策快照
映射为 `AutoReplyPolicy` 输入，并映射回 PDD decision；它不发送消息。Graph 本身没有任何
发送、队列或 connector 调用。

重复发送防护：

* `create_outbound_job` 以 `client_request_id = f"auto:{batch_key}"` 幂等，`created=False`
  时不再次入队。
* `SendUncertainError` → job `uncertain` + `_pause_after_send_failure` 关闭会话自动接待，
  不自动重试。
* Worker 异常时 `sending` job 被标记 `uncertain`，同样暂停而非重发。

## 7. Security / Data-safety Review

| Check | Result |
| --- | --- |
| secret / token / password / cookie 未提交 | PASS — tracked files 全量扫描无命中 |
| `.env` 未进入 Git | PASS — 仅 `.env.example` 被跟踪，`.env` 由 `.gitignore` 排除 |
| 日志不记录密钥 | PASS — `app/core/logging.py` 的 `_SENSITIVE_KEY` / `_SENSITIVE_ASSIGNMENT` / `_BEARER_TOKEN` 过滤器覆盖 password、token、api_key、secret、authorization、cookie |
| 长期事件/audit 不持久化完整聊天正文 | PASS — `ConsoleRuntime._sanitize_event_payload` 白名单字段；`test_event_audit_removes_message_and_reply_body` |
| customer / conversation / product 数据边界未因迁移改变 | PASS — 迁移只替换决策入口，未改 Repository 或 DB contract |
| PostgreSQL 仍是商品事实 Source of Truth | PASS — 商品读取仍经 `ProductRepository` / `ProductAnswerService` |
| MySQL 仍是客服业务事实 | PASS — conversation、message、decision、outbound job 仍在 `ConsoleRepository` |
| Milvus 仍是知识向量存储 | PASS — `QAService` hybrid retrieval 未改动 |
| Checkpoint 只保存 workflow runtime/state | PASS — `FileCheckpointStore` 只写 `AgentState` JSON，成功 Turn 清理 |
| PDD Graph 不写入错误的跨渠道商品绑定 | PASS — `session_hydrate` / `ProductLoadNode` 的 `chat_conversation_products` 读写均以 `session.channel != "pdd"` 为前置条件 |
| Send failure / UNCERTAIN 不造成重复发送 | PASS — 幂等 `client_request_id` + `uncertain` + 自动接待暂停 |

未使用 GitNexus PDG/`explain`：本次验收无需新增 source → sink 分析，安全结论来自既有
过滤器/白名单实现与其回归测试。

## 8. Full Test Results

```text
uv run pytest -q     → 533 passed, 1 warning in 22.49s
uv run ruff check .  → All checks passed
uv sync --frozen     → Checked 114 packages
git diff --check     → clean
```

唯一 warning 是既有的 Starlette TestClient `anyio.abc.BlockingPortal` 弃用提示，来自第三方
依赖，与本次验收无关。**无 skipped tests。**

按目录分布（可用 `-p no:randomly` 复现）：

| Suite | Tests |
| --- | ---: |
| `tests/agent` | 158 |
| `tests/unit` | 166 |
| `tests/rules` | 82 |
| `tests/integration` | 68 |
| `tests/qa` | 31 |
| `tests/api` | 17 |
| `tests/e2e` | 4 |
| `tests/test_bootstrap.py` + `tests/test_import_smoke.py` | 7 |
| Total | 533 |

要求覆盖的验收面与对应证据：

| Acceptance area | Evidence |
| --- | --- |
| Unified Chat API | `tests/integration/test_chat_api.py`, `tests/unit/test_chat_service.py` |
| Agent Graph | `tests/agent/test_graph_runtime.py`, `test_graph_g4_hardening.py` |
| Guard / FAQ ordering | `tests/agent/test_unified_chat_graph_parity.py`, `tests/rules/*` |
| Product | `tests/agent/test_unified_chat_graph_parity.py`, `tests/unit/test_product_*` |
| QA / RAG | `tests/qa`, `tests/unit/test_contextual_fallback_service.py` |
| Social | `tests/rules/test_social.py`, `tests/unit/test_social_router.py` |
| Fallback | `tests/unit/test_contextual_fallback_service.py`, graph parity |
| Human transfer | `tests/rules/test_human_handoff.py`, console multiroute |
| PDD parity / console | `tests/agent/test_pdd_graph_parity.py`, `tests/integration/test_console_*` |
| AutoReplyPolicy | `tests/unit/test_auto_reply_policy.py` |
| Outbound idempotency | `tests/integration/test_console_repository.py`, `test_console_runtime.py` |
| Send failed / UNCERTAIN | `tests/integration/test_console_runtime.py` |
| Checkpoint / resume | `tests/agent/test_graph_checkpoint_resume.py`, `test_state_checkpoint.py` |
| Multi-shop runtime | `tests/integration/test_multishop_runtime_manager.py` |

## 9. GitNexus Final Acceptance

```bash
node .gitnexus/run.cjs detect-changes --scope all --repo .
```

```text
Changed files = 0
Changed symbols = 0
Affected processes = 0
Result = No changes detected
```

输出不含 `partial: true` 或 `truncated: true`；索引即验收 HEAD，因此验收基线本身没有
未解释的代码风险，也没有未解释的 HIGH / CRITICAL symbol。

补充结构检查：

```text
node .gitnexus/run.cjs check --cycles  → No circular imports found.
```

用 GitNexus `query` / `context` 核对 execution flow 后，未发现第二条业务 orchestration
path：不存在第二个 Graph builder、第二个 AgentRuntime、第二个 AgentState family，也没有
在 Service / API / Channel 层重新出现的 intent、FAQ、Product、RAG、Handoff 或 workflow
选择分支。

## 10. Migration Acceptance Checklist

- [x] `/api/v1/chat` → AgentRuntime → AgentGraph。
- [x] PDD → Channel Adapter → AgentRuntime → AgentGraph。
- [x] `ChatService` 不再承担业务 workflow。
- [x] `ConsoleRuntime` 不再承担 AI workflow。
- [x] Guard、Social、Product、FAQ、RAG、Fallback、Human、Response 主要业务步骤由 Graph Node 表达。
- [x] 主要业务分支由 Conditional Edge 表达。
- [x] StatePatch 是 Node 状态更新机制。
- [x] Checkpoint 与 Node execution 对齐。
- [x] Customer Demo 与 PDD 共享相同 Agent Runtime/AgentGraph contract。
- [x] 旧 orchestrator 已删除或降级为薄 adapter。
- [x] 完整测试通过；skipped 项均有外部原因（实际无 skipped）。
- [x] Ruff 通过。
- [x] GitNexus change analysis 无未解释 HIGH/CRITICAL 风险。
- [x] 架构文档与实际实现一致。

## 11. Freeze Status

```text
G8 = COMPLETED
GRAPH_MIGRATION_FREEZE = completed
AgentGraph Migration = COMPLETED
Normal feature development may resume.
```

Freeze 解除后仍然保留的架构不变量：

1. AgentGraph 是客服业务 workflow 的唯一 orchestrator。
2. Channel / API 只做适配，不做业务决策。
3. `AgentState` / `StatePatch` 是唯一 Graph State Contract，禁止第二套 State。
4. 高风险 deterministic rules 必须经 GuardNode 在知识回答、商品生成和通用 LLM 之前运行。
5. 副作用必须经 PendingAction 与幂等检查点；`UNCERTAIN` 不自动重试。
6. PostgreSQL、MySQL、Milvus、Checkpoint 的数据职责边界不变。

后续如需回退迁移结果，使用 Git revert，而不是重新引入运行时 rollback switch。
