# tmt_service Agent Rules

## 1. Authority And Source Of Truth

平台级安全与系统指令始终优先。仓库内规则冲突按以下顺序处理：

```text
Security / Data Safety
  > AGENTS.md architecture invariants and Migration Freeze
  > GitNexus change protocol
  > Testing / Validation
  > Git Commit / Push
  > task-level implementation preferences
```

任务级 prompt 可以缩小任务范围；只有在当前任务明确要求例外，且不违反安全或数据安全时，
才能覆盖本文明确允许例外的条款。不得用普通功能需求隐式绕过 Migration Freeze。

`docs/AGENT_GRAPH_MIGRATION.md` 是迁移阶段、状态、步骤和验收证据的唯一 Source of Truth；
本文是开发决策与架构不变量的 Source of Truth。两者不一致时停止相关实现，先报告并修正
文档冲突，不得自行选择较宽松解释。

当前阶段必须在每个任务开始时从迁移文档读取，不得依赖本文的历史快照。

## 2. Architecture Invariants

### Migration Freeze

`GRAPH_MIGRATION_FREEZE = active` 期间只允许：

- AgentGraph migration 本身；
- 迁移必需的 adapter、compatibility cleanup 和 legacy removal；
- migration 引入或阻断 migration 的 regression fix；
- 支撑迁移的测试、文档和可观测性；
- 安全、数据安全和数据正确性级别的必要修复。

默认禁止：

- 新客服能力、业务流程、Tool、Memory、Intent 或自动回复 workflow；
- 新 channel-specific business orchestrator；
- Legacy 路由新增业务分支；
- 第二套 AgentGraph、AgentState、AgentRuntime 或长期并行决策路径；
- 以“临时兼容”为名绕过 AgentGraph。

若受限能力可以等待 Graph 基础完成，则不得在 Freeze 期间实现。兼容抽象必须属于目标架构，
或写明可验证的移除条件和对应迁移阶段。

### Runtime And Orchestration

目标调用链唯一为：

```text
Channel / API -> Channel Adapter -> AgentRuntime -> AgentGraph
    -> Node / Conditional Edge -> Service / Repository / Tool
    -> Database / LLM / External System
```

AgentGraph 是客服业务 workflow 的唯一 orchestrator。Workflow Step 用 Node，业务分支用
Conditional Edge；Node 读取 `AgentState`、调用已有能力并返回 `StatePatch`，不得复制能力
实现或成长为巨型 Service。

不得在 `ChatService._route_chat()`、`ConsoleRuntime._evaluate_batch()` 或其他 legacy
orchestrator 中新增以下任何逻辑：

- intent classification 或 routing branch；
- handoff / complaint / after-sale decision；
- FAQ、Product、RAG、Fallback 或 Social dispatch；
- channel-specific business decision 或新的 AI workflow。

这些位置只允许删除/迁移旧逻辑、薄适配、调用 `AgentRuntime` / `AgentGraph`、保持行为的
bug fix，以及渠道侧 transport、persistence、send queue 和 lifecycle 管理。完成一条 Graph
路径后，不得长期保留可独立运行的同类 legacy 决策链。

可复用能力继续作为 Service、Repository、Tool 或 Infrastructure，例如 `ProductResolver`、
`QAService`、`RuleRegistry`、`AutoReplyPolicy`、Repository、Factory、VectorStore 和 Connector。
关系必须保持 `Node -> Service -> Repository / Tool -> Infrastructure`。

### State, Safety And Persistence

`AgentState`、`ChatSessionState`、`ChatTurnState` 和 `StatePatch` 是唯一 Graph State
Contract。Session State 只保存引用和长期运行状态；Turn State 保存当前请求状态。禁止长期
复制完整商品数据或无限聊天历史到 State。

PostgreSQL、MySQL、Milvus 和 Checkpoint 分别保存商品事实、客服业务事实、向量知识和 Workflow
Runtime。Checkpoint 必须对齐 before/after/failed/resume Node 生命周期，并保留 revision、
retry、resume、PendingAction、幂等和 `UNCERTAIN` 安全语义。

`HumanHandoffRule`、`ComplaintRule`、`AfterSaleRiskRule` 等高风险 deterministic rules 必须
经 GuardNode 在知识回答、商品生成和通用 LLM 之前运行。任何现有顺序差异必须先有
characterization test，再按迁移计划调整；禁止静默改变行为。

## 3. Change Protocol

### Before Editing

1. 读取 `git status --short`，记录并保护用户已有 WIP。不得覆盖、回滚、暂存或提交无关修改。
2. 检查迁移文档中的当前阶段和本任务是否在 Freeze 允许范围内。
3. 检查 GitNexus freshness。若索引 commit 与当前 `HEAD` 不一致，或结构性修改后还要继续
   做图分析，先运行 `node .gitnexus/run.cjs analyze --index-only`。
4. 修改任何已索引 function、class、method 或其他 code symbol 前，必须运行 upstream `impact`
   并报告 callers、processes 和 risk。纯文档、静态资源和不可索引配置无需 symbol impact；若
   它们可能改变运行时行为，必须用 `query` / `context` 检查相关 flow。
5. 理解概念/flow 用 `query`，读取命名 symbol 用 `context`，blast radius 用 `impact`。Graph
   first；仅在结果为空、`UNKNOWN`、动态属性、跨语言边界或搜索 literal 时补充 `rg`。

CLI fallback：

```bash
node .gitnexus/run.cjs query "<concept>" --repo .
node .gitnexus/run.cjs context "<symbol>" --repo .
node .gitnexus/run.cjs impact "<symbol>" --direction upstream --repo .
```

Risk 处理必须遵守：

- `LOW` / `MEDIUM`：可以继续；验证直接 callers 和受影响 execution flows。
- `HIGH`：先报告 blast radius 并缩小修改面；允许继续，但必须增加受影响流程验证。
- `CRITICAL`：默认不得直接修改。只有任务明确需要且没有低风险替代时才能继续；必须说明
  原因并验证全部已识别 execution flows。
- `UNKNOWN`：未解决，不是低风险。依次执行 `rg` 搜索 symbol/property/literal，再用 `query` /
  `context` 查概念和 flow；找到调用后按实际 blast radius 处理，未找到仍标记 `unresolved`。
  `UNKNOWN` 或空 caller 集绝不能作为 symbol 未使用或可删除的依据。

`partial: true`、`truncated: true` 或分析预算截断均不是 clean result；调整范围后重跑。仍
无法得到完整结果时必须标记 unresolved，并采用保守验证范围。HIGH/CRITICAL 不得用
`riskSharedAxes` 抵消。重命名必须使用 graph-aware `rename`，禁止 find-and-replace。

### During Editing

- 选择推进当前迁移阶段的最小架构变更；不做机会式重构。
- 遵循 Behavior-Preserving Migration：先建立 characterization/regression baseline，再迁移
  路径，之后才优化业务。
- 新客服需求编码前必须完成：Workflow -> Graph/Subgraph；Step -> Node；Branch -> Conditional
  Edge；Reusable Capability -> Service；External Action -> Tool；Persistence -> Repository。
- 若任务会新增 legacy branch、第二套 state/graph/runtime 或绕过 AgentGraph，立即停止并报告
  架构冲突；测试通过不能豁免架构不变量。

### Verification

每个实现至少验证：

1. changed behavior 的 targeted tests；
2. GitNexus 报告的 callers 和 affected flows 的 regression tests；
3. 当前迁移阶段的 architecture invariants；
4. 没有新增 Legacy orchestrator branch；
5. 没有第二套 AgentState、AgentGraph 或 runtime path；
6. `uv run ruff check .` 和 `git diff --check`；
7. commit 前完整运行 `detect-changes --scope all`。

默认运行 `uv run pytest -q`；成本过高时先跑相关测试并尽可能补跑全量。State、Checkpoint、
Repository、API、Database、RAG 或 Agent 变更必须优先跑对应测试。由本次修改导致的失败
必须先修复，不得提交。外部依赖导致的失败可以继续，但最终必须准确标记 `BLOCKED` 或
`SKIPPED` 及原因，不能写成 PASS。

修改过程中可按需运行 `detect-changes --scope all`。PR/合并前必须额外运行：

```bash
node .gitnexus/run.cjs detect-changes --scope compare --base-ref "<target-branch>" --repo .
```

`<target-branch>` 必须取实际合并目标，不得硬编码为 `master`。任何 detect-changes 的
partial/truncated 输出必须重跑；未解释的 HIGH/CRITICAL 风险阻止提交或合并。

## 4. Migration Acceptance

只有 `docs/AGENT_GRAPH_MIGRATION.md` 中的所有验收证据齐全，才能把 Freeze 改为 `completed`，
至少包括：

1. `/api/v1/chat` 和 PDD 都经 AgentRuntime 进入 AgentGraph；
2. `ChatService` 不再承担业务 workflow，`ConsoleRuntime` 不再承担 AI workflow；
3. Guard、Social、Intent、Product、FAQ、RAG、Fallback、Human、Response 由 Graph Node 表达，
   主要分支由 Conditional Edge 表达；
4. `StatePatch` 是 Node 更新机制，Checkpoint 与 Node execution 对齐；
5. Customer Demo 与 PDD 共享同一 AgentRuntime；
6. legacy orchestrator 已删除或降级为 adapter；
7. tests、Ruff、architecture checks 和 GitNexus change analysis 通过，无未解释风险；
8. 架构文档与实际实现一致。

## 5. Commit And Push

代码或规则修改完成且验证通过后，除非当前任务明确要求不提交、不 push、只本地修改或只分析，
否则自动提交当前任务产生的文件，并依次 push：

```bash
git push origin master   # Gitee
git push github master   # GitHub
```

提交前必须执行并检查：

```bash
git status --short
git diff --check
git diff
node .gitnexus/run.cjs detect-changes --scope all --repo .
git add <only-task-files>
git diff --cached --check
git diff --cached
```

不得用 `git add -A` 带入用户 WIP。检查新文件已被跟踪，且没有 `.env`、key、token、cookie、
password、private key、真实数据库凭据、浏览器数据、隐私数据、运行时文件或意外大文件。发现
疑似 secret 时立即停止 commit/push，并报告：`BLOCKED: 检测到可能的敏感信息`。

Commit message 使用 `<type>(<scope>): <description>`。禁止空 commit。禁止未经明确授权执行
force push、hard reset、clean、整树 restore、历史重写、删除用户修改或远程分支。non-fast-forward
时先检查 status/log/fetch；不能安全判断则停止 push 并报告。

push 后运行 `git status -sb`、`git log -1 --oneline` 和 `git remote -v`，报告测试、lint、
diff-check、GitNexus、branch、commit SHA、两个远程结果及 working tree 状态；任何失败或未执行项
必须如实列出。

## GitNexus Reference

| Purpose | Resource / Skill |
| --- | --- |
| Repository overview / freshness | `gitnexus://repo/tmt_service/context` |
| Functional areas | `gitnexus://repo/tmt_service/clusters` |
| Execution flows | `gitnexus://repo/tmt_service/processes` |
| One execution trace | `gitnexus://repo/tmt_service/process/{name}` |
| Architecture exploration | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Impact analysis | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Debugging | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Refactoring / rename | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| CLI / schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index maintenance | `.claude/skills/gitnexus-cli/SKILL.md` |

Security review uses `explain({target: "fileOrSymbol"})` after PDG analysis to inspect taint
source-to-sink flows.
