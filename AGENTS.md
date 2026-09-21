# tmt_service 项目开发规则

本规则适用于 `tmt_service` 仓库内所有代码修改、重构、测试、迁移和架构调整。

当前项目处于 **Agent Graph Migration Freeze** 阶段。

开发目标不是单纯“让功能跑通”，而是：

> 在保证现有功能稳定的前提下，持续把客服工作流从 Legacy Orchestrator 收敛到 AgentGraph，禁止继续扩大旧架构。

---

# 1. 规则优先级

当多个规则、文档或任务要求发生冲突时，按以下优先级执行：

1. 平台级安全、数据安全和系统指令。
2. 本文件中的架构不变量与 Agent Graph Migration Freeze。
3. `docs/AGENT_GRAPH_MIGRATION.md`。

   * 该文档是迁移阶段、状态、步骤、验收证据的唯一 Source of Truth。
4. GitNexus 分析结果和开发流程要求。
5. 测试与验证要求。
6. 当前任务 Prompt 中的实现要求。
7. 开发者自行判断的实现偏好。

本文是仓库完整开发规范和架构不变量的主要 Source of Truth；迁移文档只对迁移阶段、
状态、步骤和验收标准具有最终决定权。两者不一致时必须停止相关实现，先报告并修正文档
冲突，不得自行选择较宽松解释。低优先级规则不得违反高优先级规则。

任何任务开始修改代码前，必须先阅读：

```text
AGENTS.md
docs/AGENT_GRAPH_MIGRATION.md
```

如果任务涉及 AgentGraph、State、Runtime、Chat、Console、Rule、RAG、QA 或客服路由，
必须确认当前迁移阶段后再开始实现。

# 2. 当前架构原则

当前项目所有客服 Workflow 必须向：

```text
AgentGraph
```

收敛。

禁止因为“实现方便”在 ChatService、ConsoleRuntime、API 或 Service 层恢复客服业务编排。

G6 已删除的 Legacy Orchestrator 包括：

```text
ChatService._route_chat()
ConsoleRuntime._evaluate_batch_legacy()
```

不得恢复、重建等价分支或重新引入 runtime rollback switch。客服业务决策只能进入
共享 AgentGraph。

---

# 3. Agent Graph Migration Freeze

当前处于：

```text
Agent Graph Migration Freeze
```

冻结期间，默认禁止开发与迁移无关的新客服业务能力。

## 允许开发的内容

以下修改允许进行：

* AgentGraph 迁移；
* AgentState / Runtime 的迁移工作；
* 将 Legacy Workflow 迁入 AgentGraph；
* 删除旧 orchestrator 中已经迁移的逻辑；
* 为迁移必需的 adapter；
* 为迁移必需的 compatibility layer；
* 修复 AgentGraph 迁移引入的 regression；
* 迁移相关测试；
* 迁移相关日志和 observability；
* 迁移相关文档；
* 数据正确性问题；
* 安全问题；
* 阻塞当前迁移阶段的必要 bugfix。

## 禁止开发的内容

禁止：

* 新增与 AgentGraph 迁移无关的客服业务；
* 新增独立 Workflow Engine；
* 新增第二套 AgentGraph；
* 新增第二套 AgentState；
* 新增第二套 Runtime；
* 新增长期存在的兼容 orchestrator；
* 在 Legacy 层增加新的业务路由；
* 为单独 Channel 建立业务 orchestrator；
* 为临时需求绕开 AgentGraph；
* 将本应进入 AgentGraph 的决策逻辑塞进 API / Service / Runtime。

任何新的客服业务决策，默认都应该进入 AgentGraph。

---

# 4. Legacy Orchestrator 复活禁止

以下方法已在 G6 删除：

```text
ChatService._route_chat()
ConsoleRuntime._evaluate_batch_legacy()
```

禁止在 Facade、Channel Runtime、API dependency 或 Service 中恢复：

* intent 判断；
* FAQ 路由；
* Product 路由；
* RAG 路由；
* Tool Selection；
* Handoff 决策；
* Business Rule 分支；
* Channel 特有逻辑；
* Workflow Selection；
* Agent 选择；
* State Machine；
* 新业务条件判断；
* 多轮对话状态判断。

例如禁止继续出现类似：

```python
if intent == "refund":
    ...

elif intent == "product":
    ...

elif channel == "console":
    ...

elif should_use_rag:
    ...
```

这类决策应该进入 AgentGraph 或对应的 AgentGraph Node。当前合法边界是：

```text
Channel / API
    ↓
Facade / Channel Adapter
    ↓
AgentRuntime / AgentGraph
    ↓
Channel / API presentation
```

---

# 5. GitNexus：Graph First

项目已经由 GitNexus 建立代码图谱。

所有涉及：

* 架构；
* 依赖；
* caller；
* execution flow；
* blast radius；
* symbol 修改；
* 重构；

的问题，必须优先使用 GitNexus。

禁止直接用：

```text
grep
rg
IDE Find References
```

代替 GitNexus Graph Analysis。

文本搜索只能作为补充手段。

这里禁止的是用文本搜索替代架构图分析；当 Graph 结果为空、UNKNOWN、动态属性、跨语言边界
或需要检索 literal 时，必须按本规则补充使用 `rg`，并结合 query / context / impact
确认真实影响范围。

---

# 6. 修改代码前必须进行 Impact Analysis

修改以下对象之前：

* function；
* method；
* class；
* runtime behavior symbol；
* orchestrator；
* service；
* agent；
* graph node；

必须先运行 GitNexus Impact Analysis。

例如：

```bash
node .gitnexus/run.cjs impact "symbolName" \
  --direction upstream \
  --repo .
```

或者使用对应 MCP：

```text
impact({
  target: "symbolName",
  direction: "upstream"
})
```

修改前必须明确：

* 谁调用这个 symbol；
* 影响哪些 execution flows；
* GitNexus 风险等级；
* 是否位于核心链路；
* 是否会影响迁移架构。

不得先修改代码，再补 impact。

---

# 7. GitNexus 工具使用规则

## 已知 symbol

使用：

```text
context({name: "symbolName"})
```

## 分析影响范围

使用：

```text
impact({
  target: "symbolName",
  direction: "upstream"
})
```

## 查业务流程 / 概念

使用：

```text
query({
  search_query: "concept"
})
```

适用于：

* QA flow；
* Product flow；
* Chat flow；
* AgentGraph flow；
* Console flow；
* RAG flow；
* Handoff flow。

## 安全审查

需要 source → sink 分析时使用：

```text
explain({
  target: "fileOrSymbol"
})
```

如需要 PDG，应先保证 GitNexus 使用 PDG 模式完成索引。

---

# 8. GitNexus UNKNOWN 处理

以下结果：

```text
risk: UNKNOWN
```

绝不代表：

```text
LOW
```

也不代表：

```text
没有调用方
```

UNKNOWN 的含义只能理解为：

> 当前 Graph 无法确认真实影响范围。

可能原因包括：

* dynamic dispatch；
* plain-object property access；
* framework registration；
* decorator；
* reflection；
* dependency injection；
* config-driven dispatch；
* cross-language invocation；
* generated code。

如果出现 UNKNOWN：

第一步，使用文本搜索：

```bash
rg "symbolName"
```

第二步，搜索相关：

* property name；
* config key；
* route name；
* event name；
* literal；
* registry；
* import；
* callback。

第三步，再结合：

```text
query(...)
context(...)
```

检查相关 Flow。

即使最终没有搜到调用方，也只能标记：

```text
Impact unresolved
```

不得直接判断：

```text
Unused
```

特别禁止：

> 因为 GitNexus 返回 0 callers 就删除 symbol。

---

# 9. GitNexus 风险等级处理

## LOW

允许修改。

完成：

* targeted tests；
* 基本 regression；

即可。

## MEDIUM

允许修改。

必须确认：

* 上游 callers；
* 受影响 flow；
* regression coverage。

## HIGH

修改前必须明确报告：

```text
HIGH RISK
```

并说明：

* callers；
* execution flows；
* 为什么必须修改；
* 是否存在更低风险实现。

优先选择更小的修改方案。

修改后必须验证相关 execution flows。

## CRITICAL

CRITICAL Symbol 默认视为架构热点。

不得因为“改这里最方便”就修改。

只有满足以下条件才允许修改：

* 当前任务确实要求；
* 无合理低风险替代实现；
* 修改不会扩大 Legacy Architecture；
* 修改前明确记录风险；
* 修改后验证主要 affected flows。

如果可以通过外围 adapter、调用点或迁移方式解决，则优先避免修改 CRITICAL symbol。

不得使用：

```text
riskSharedAxes
```

来降低 HIGH / CRITICAL 的风险判断。

---

# 10. GitNexus Index Freshness

开始复杂架构任务前，应检查 GitNexus Index 是否与当前代码一致。

优先查看：

```text
gitnexus://repo/tmt_service/context
```

如果：

* HEAD 已发生明显变化；
* 大量代码刚完成 merge；
* 刚完成大规模重构；
* GitNexus Index 对应版本落后；

应运行：

```bash
node .gitnexus/run.cjs analyze --index-only
```

确保后续 impact 和 flow 分析基于当前代码。

---

# 11. 修改前必须保护用户 WIP

每次开始修改前必须执行：

```bash
git status --short
git branch --show-current
git rev-parse --short HEAD
```

必须确认：

* 当前 branch；
* 当前 HEAD；
* 当前 working tree；
* 是否存在用户未提交修改。

如果存在用户 WIP：

禁止：

```text
git reset --hard
git checkout .
git restore .
git clean -fd
```

除非用户明确要求。

不得：

* 覆盖用户修改；
* 回滚用户修改；
* stage 无关文件；
* commit 无关文件；
* 格式化整个仓库导致无关 diff。

任务只允许修改完成任务必要的文件。

---

# 12. 最小修改原则

默认采用：

```text
Smallest Correct Change
```

不要因为当前任务顺手进行：

* 无关重构；
* 全项目 rename；
* 全局 formatting；
* architecture cleanup；
* dependency upgrade；
* unrelated optimization。

除非：

* 当前修改无法安全实现；
* 当前迁移阶段明确要求；
* 用户明确要求。

尤其在 Migration Freeze 阶段，应优先：

```text
迁移旧逻辑
```

而不是：

```text
创建新的抽象层
```

---

# 13. 禁止复制第二套架构

项目迁移期间特别禁止通过复制现有逻辑形成：

```text
旧系统
+
新系统
```

长期并存。

例如禁止形成：

```text
Chat AgentGraph
Console AgentGraph
Customer Demo AgentGraph
```

如果它们本质属于同一个客服工作流，应共享统一的 AgentGraph / Runtime。

不同入口可以拥有：

```text
Adapter
Transport
Input Mapping
Output Mapping
```

但不得分别维护业务决策。

---

# 14. Channel 与业务逻辑分离

Web、Console、Customer Demo 或其他 Channel，只应该负责：

* request parsing；
* authentication；
* transport；
* payload adaptation；
* session mapping；
* response presentation。

Channel 不应该决定：

* 调哪个 Agent；
* 是否走 FAQ；
* 是否走 RAG；
* 是否 Product QA；
* 是否 Handoff；
* 下一步 workflow；
* 当前 AgentState。

这些业务判断应属于 AgentGraph。

---

# 15. State 单一来源

多轮客服状态必须逐步收敛到统一 AgentState。

禁止创建：

```text
console_state
chat_state
customer_demo_state
legacy_state
temporary_state
```

等互相独立的状态体系。

如果某个 Channel 需要额外 metadata，应作为：

```text
channel metadata
```

而不是新的 Workflow State。

---

# 16. 兼容层规则

Migration 期间允许 Compatibility Layer，但必须满足：

```text
Thin
Explicit
Temporary
Removable
```

兼容层不能：

* 自己做 workflow orchestration；
* 自己判断业务 intent；
* 自己决定下一 Agent；
* 自己维护另一套 State；
* 长期演变成第二套 Service。

如果引入新的 compatibility abstraction，应明确：

```text
迁移完成后由什么条件删除
```

---

# 17. 测试要求

每次代码修改至少需要：

## Targeted Tests

验证当前修改目标。

例如：

```text
test_agent_graph_xxx
test_chat_xxx
test_console_xxx
```

## Regression Tests

针对 GitNexus Impact Analysis 返回的关键调用链进行验证。

如果 impact 显示：

```text
A
 ↓
B
 ↓
ChangedSymbol
```

至少验证：

```text
A → B → ChangedSymbol
```

所在的重要业务流程。

---

# 18. AgentGraph Migration 验收

测试通过不代表任务完成。

还必须检查：

* 是否违反当前 Migration Phase；
* 是否新增 Legacy Orchestrator 分支；
* 是否产生第二套 State；
* 是否产生第二套 Runtime；
* 是否产生第二套 AgentGraph；
* 是否新增 Channel-specific Workflow；
* 是否把本该进入 Graph 的决策留在 Service 层。

如果：

```text
Tests = PASS
Architecture = FAIL
```

任务仍然视为：

```text
FAIL
```

---

# 19. 修改完成后必须运行 GitNexus Detect Changes

提交代码前必须运行：

```bash
node .gitnexus/run.cjs detect-changes \
  --scope all \
  --repo .
```

必须检查：

* affected symbols；
* affected flows；
* graph changes；
* risk；
* 是否出现意外影响。

如果输出包含：

```text
partial: true
```

或：

```text
truncated: true
```

不得认为分析完成。

必须重新执行或缩小分析范围。

一个 incomplete result 中：

```text
0 affected
```

不能解释为：

```text
没有影响
```

---

# 20. Merge / Regression 前比较目标分支

准备 merge 时，必须执行：

```bash
node .gitnexus/run.cjs detect-changes \
  --scope compare \
  --base-ref "<target-branch>" \
  --repo .
```

`<target-branch>` 应使用真实 merge target。

例如：

```text
master
develop
release/*
```

不要默认永远使用 `master`。

---

# 21. Rename / Refactor

禁止使用全局 find-and-replace 重命名代码 symbol。

例如禁止：

```text
Search: old_method
Replace All: new_method
```

应使用 GitNexus-aware rename / refactoring 工具。

涉及：

* rename；
* extract；
* split；
* move；
* architectural refactor；

应先查看：

```text
.claude/skills/gitnexus-refactoring/SKILL.md
```

---

# 22. Bug 修复流程

发现 Bug 时不要直接修改看到的第一段代码。

默认流程：

```text
复现问题
    ↓
找到失败入口
    ↓
GitNexus 查询 execution flow
    ↓
定位实际 decision point
    ↓
对目标 symbol 做 impact
    ↓
确认是否违反 AgentGraph migration boundary
    ↓
最小修复
    ↓
targeted test
    ↓
regression test
    ↓
detect-changes
```

尤其禁止为了快速修复：

```text
在 _route_chat() 再加一个 if
```

或：

```text
在 _evaluate_batch() 再加一个特殊判断
```

除非该修改属于删除、迁移或纯适配。

---

# 23. 新功能判断

如果任务要求新增客服能力，在 Migration Freeze 阶段必须先判断：

```text
这个能力是否属于当前 AgentGraph Migration？
```

如果不是：

默认不得新增。

如果属于迁移范围：

业务决策进入：

```text
AgentGraph
```

入口层只负责调用。

---

# 24. 文档同步

如果修改涉及：

* AgentGraph topology；
* AgentState contract；
* Runtime boundary；
* Legacy migration state；
* migration acceptance criteria；

必须同步检查：

```text
docs/AGENT_GRAPH_MIGRATION.md
```

如果迁移状态发生实际变化，应更新对应文档。

不要让：

```text
代码已经迁移
文档仍显示未迁移
```

或反过来。

---

# 25. Commit 前最终检查

提交之前至少完成：

```text
[ ] 阅读并遵循 AGENTS.md
[ ] 确认当前 AgentGraph Migration Phase
[ ] 检查 git status / branch / HEAD
[ ] 用户原有 WIP 未被覆盖
[ ] 修改目标 symbol 已运行 impact
[ ] HIGH / CRITICAL 已处理
[ ] UNKNOWN 已补充 text search
[ ] 没有扩张 Legacy Orchestrator
[ ] 没有新增第二套 Graph / State / Runtime
[ ] Targeted tests 通过
[ ] 关键 affected-flow regression 通过
[ ] detect-changes --scope all 已运行
[ ] detect-changes 无未解释的重要风险
[ ] Migration architecture invariant 通过
```

准备 Merge 时另外检查：

```text
[ ] detect-changes --scope compare 已运行
[ ] 使用了正确 target branch
[ ] Migration acceptance criteria 仍然满足
```

---

# 26. 默认开发流程

所有正常开发任务默认采用以下流程：

```text
读取 AGENTS.md
        ↓
读取 Migration 状态
        ↓
检查 Working Tree
        ↓
理解现有代码和 Execution Flow
        ↓
GitNexus Query / Context
        ↓
GitNexus Impact
        ↓
确认 Migration Boundary
        ↓
选择最小合规实现
        ↓
修改代码
        ↓
Targeted Tests
        ↓
Affected Flow Regression
        ↓
GitNexus Detect Changes
        ↓
Architecture Verification
        ↓
Commit / Merge
```

禁止采用：

```text
看到代码
  ↓
直接修改
  ↓
测试能过
  ↓
完成
```

---

# 27. 开发完成标准

一个任务只有同时满足：

```text
Behavior Correct
+
Tests Pass
+
Graph Impact Reviewed
+
Migration Architecture Compliant
+
User WIP Preserved
```

才算真正完成。

当前项目最重要的长期原则是：

> 新代码应持续减少 Legacy Orchestrator 的职责，并使客服 Workflow 更接近统一 AgentGraph，而不是创造新的过渡架构。


---

# 28. Runtime、State 与持久化不变量

客服请求的唯一目标调用链为：

```text
Channel / API
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

AgentGraph 是客服业务 workflow 的唯一 orchestrator。Node 读取 `AgentState`、调用已有能力并
返回 `StatePatch`；Node 不得复制 Service 实现或成长为巨型 Service。Legacy
orchestrator 只能做输入/输出适配、transport、persistence、send queue 和 lifecycle 管理，
不得恢复独立的业务决策链。

`AgentState`、`ChatSessionState`、`ChatTurnState` 和 `StatePatch` 是唯一 Graph State
Contract。Session State 只保存引用和长期运行状态，Turn State 保存当前请求状态；禁止长期
复制完整商品数据或无限聊天历史到 State。

PostgreSQL、MySQL、Milvus 和 Checkpoint 分别保存商品事实、客服业务事实、向量知识和
Workflow Runtime。Checkpoint 必须对齐 Node 的 before/after/failed/resume 生命周期，并
保留 revision、retry、resume、PendingAction、幂等和 `UNCERTAIN` 安全语义。副作用 Tool
必须经过 PendingAction 和幂等检查点。

`HumanHandoffRule`、`ComplaintRule`、`AfterSaleRiskRule` 等高风险 deterministic
rules 必须经 GuardNode 在知识回答、商品生成和通用 LLM 之前运行。任何现有顺序差异都必须
先有 characterization test，再按迁移计划调整；禁止静默改变行为。

---

# 29. Migration Acceptance 与文档同步

只有 `docs/AGENT_GRAPH_MIGRATION.md` 中的所有验收证据齐全，才能把
`GRAPH_MIGRATION_FREEZE` 改为 `completed`。至少包括：

1. `/api/v1/chat` 和 PDD 都经 AgentRuntime 进入 AgentGraph。
2. `ChatService` 不再承担业务 workflow，`ConsoleRuntime` 不再承担 AI workflow。
3. Guard、Social、Intent、Product、FAQ、RAG、Fallback、Human、Response 由 Graph Node
   表达，主要分支由 Conditional Edge 表达。
4. `StatePatch` 是 Node 更新机制，Checkpoint 与 Node execution 对齐。
5. Customer Demo 与 PDD 共享同一 AgentRuntime。
6. legacy orchestrator 已删除或降级为 adapter。
7. tests、Ruff、architecture checks 和 GitNexus change analysis 通过，无未解释风险。
8. 架构文档与实际实现一致。

修改涉及 AgentGraph topology、AgentState contract、Runtime boundary、Legacy migration
state 或 migration acceptance criteria 时，必须同步检查迁移文档。只有实现证据真实变化时
才更新其状态，不得把 Planned 描述成 Implemented，也不得仅因测试通过就解除 Freeze。

---

# 30. Commit、Push 与敏感信息保护

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

不得用 `git add -A` 带入用户 WIP。检查新文件已被跟踪，且没有 `.env`、key、token、
cookie、password、private key、真实数据库凭据、浏览器数据、隐私数据、运行时文件或意外
大文件。发现疑似 secret 时立即停止 commit/push，并报告：

```text
BLOCKED: 检测到可能的敏感信息
```

Commit message 使用 `<type>(<scope>): <description>`。禁止空 commit。禁止未经明确授权
执行 force push、hard reset、clean、整树 restore、历史重写、删除用户修改或远程分支。
non-fast-forward 时先检查 status/log/fetch；不能安全判断则停止 push 并报告。

push 后运行 `git status -sb`、`git log -1 --oneline` 和 `git remote -v`，报告测试、
lint、diff-check、GitNexus、branch、commit SHA、两个远程结果及 working tree 状态；任何
失败或未执行项必须如实列出。

---

# 31. GitNexus Reference

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

需要 source → sink 安全审查时，先按 GitNexus PDG 规则完成索引，再使用
`explain({target: "fileOrSymbol"})` 检查 taint flow。
