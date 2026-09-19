<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **tmt_service** (2125 symbols, 4398 relationships, 173 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "master"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "master" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/tmt_service/context` | Codebase overview, check index freshness |
| `gitnexus://repo/tmt_service/clusters` | All functional areas |
| `gitnexus://repo/tmt_service/processes` | All execution flows |
| `gitnexus://repo/tmt_service/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

---

# Agent Graph Architecture Rules

## Rule Priority

后续任务必须按以下优先级解决冲突：

```text
Security / Data Safety
        ↓
Agent Graph Architecture Rules
        ↓
GitNexus Impact Rules
        ↓
Testing / Validation
        ↓
Git Commit / Push
```

普通任务要求与 Migration Freeze 冲突时，必须优先遵守 Migration Freeze。
只有用户在当前具体任务中明确要求例外时，才能按该任务的显式授权执行。

## Current Phase

项目正式进入：

```text
Phase 2 — Unified Agent Graph Migration
GRAPH_MIGRATION_FREEZE = active
```

当前真实架构仍以 `ChatService._route_chat()` 和 `ConsoleRuntime._evaluate_batch()`
作为主要程序式客服编排。`app/agent/` 中的 State、StatePatch、Coordinator 和
FileCheckpointStore 是迁移基础，但 `app/agent/graph.py` 尚未实现，Agent Graph
还没有成为主 Runtime。

唯一迁移计划位于 `docs/AGENT_GRAPH_MIGRATION.md`。在迁移验收完成前，默认暂停
所有非迁移必需的新业务功能开发。

## Freeze Scope

Migration Freeze 解除前，默认禁止新增：

* 新业务流程
* 新客服渠道业务编排
* 新 QA 路由分支
* 新商品路由分支
* 新售后流程
* 新 Tool 业务
* 新 Memory 业务
* 新 Intent workflow
* 新自动回复 workflow

允许的例外仅限于：

* 迁移所需基础设施
* Bug 修复
* 安全修复
* 数据安全修复
* 阻断迁移的问题
* 测试与可观测性改进

如果用户要求新增上述受限能力，必须先判断该能力能否等待 Graph 基础完成后再实现。
原则上不得继续扩大 `ChatService._route_chat()`、`ConsoleRuntime._evaluate_batch()`
或其他旧式 orchestrator。

## Target Architecture

项目最终统一为：

```text
Channel / API
      ↓
AgentRuntime
      ↓
Agent Graph
      ↓
Node
      ↓
Conditional Edge
      ↓
Service / Repository / Tool
      ↓
Database / LLM / External System
```

Agent Graph 是客服业务 workflow 的唯一 orchestrator，负责节点顺序、条件分支、
恢复路径、结束条件、人工路径、Tool 路径、知识路径和商品路径。禁止在 Graph 之外
建立第二套长期客服业务 orchestrator。

客服决策链路中的 Workflow Step 必须由 Node 表达。Node 读取 `AgentState`，调用
已有能力，生成明确结果，并返回 `StatePatch`；Node 不得变成新的巨型 Service。
业务流程分支原则上必须由 Conditional Edge 表达；普通能力内部的小范围条件判断
不受此限制。

## Graph-first Is Not Node-everything

以下能力应继续保持为普通 Service、Repository、Tool 或 Infrastructure：

* ProductResolver / SemanticProductResolver / ProductRepository
* ProductAnswerService / QAService / SocialRouter / RuleRegistry
* ContextualFallbackService / AutoReplyPolicy
* MessageRepository / ConversationRepository
* LLMFactory / EmbeddingFactory / VectorStore / Connector

正确关系是：

```text
Node
 ↓
Service
 ↓
Repository / Tool
 ↓
Infrastructure
```

例如 `ProductResolveNode` 决定什么时候调用 `ProductResolver` 以及调用后走向哪里；
`ProductResolver` 继续负责如何解析商品。不要把可复用能力复制进 Node。

## State And Checkpoint

`AgentState`、`ChatSessionState`、`ChatTurnState` 和 `StatePatch` 是唯一 Graph
State Contract。禁止另建第二套 Graph State。新 Graph Node 优先读取 `AgentState`
并返回 `StatePatch`，不得直接修改多个全局对象。

必须继续遵守：

* Session State 保存引用和长期运行状态。
* Turn State 保存当前请求状态。
* PostgreSQL 保存商品事实。
* MySQL 保存客服业务事实。
* Milvus 保存向量知识。
* Checkpoint 保存 Workflow Runtime。

禁止把完整商品数据长期复制到 Session State，禁止把完整聊天历史无限复制到 State。
最终 Checkpoint 必须围绕真实 Node 生命周期保存 before node、after node、failed node
和 resume node 状态，并保留 revision、retry、resume、PendingAction 和 UNCERTAIN
恢复安全语义。

## Safety And Runtime Convergence

`HumanHandoffRule`、`ComplaintRule` 和 `AfterSaleRiskRule` 等高风险 deterministic
rules 必须通过 GuardNode 在知识回答、商品生成和通用 LLM 之前运行。现有 Unified
Chat 中 FAQ exact 先于 Rule 的行为属于迁移事实；必须先建立回归测试，再统一为
安全优先顺序，不得静默改变行为。

第一版 Graph 保持粗粒度业务 Node，不得为了“Graph 化”拆出几十个无意义 Node。
最终 Unified Chat 和 PDD 必须经过 Channel Adapter 共享同一个 AgentRuntime 和
AgentGraph。`ChatService` 最终降级为请求适配、运行上下文管理和响应映射 Facade；
`ConsoleRuntime` 最终只负责店铺 Runtime、Connector 生命周期、消息接收、发送队列、
发送结果和渠道状态。

新增客服功能编码前必须先完成 Graph-first 设计检查：

```text
Workflow            → Graph / Subgraph
Workflow Step       → Node
Branch              → Conditional Edge
Reusable Capability → Service
External Action     → Tool
Persistence         → Repository
```

迁移期间遵循 Behavior-Preserving Migration：先迁移架构，再优化业务。完成某条
Graph 路径后不得长期保留两套可独立运行的业务逻辑；Migration Acceptance 时旧
orchestrator 的业务编排职责必须删除或降级为薄适配层。

## Migration Acceptance

只有同时满足以下条件，才能把 `GRAPH_MIGRATION_FREEZE` 从 `active` 改为
`completed`：

1. `/api/v1/chat` 和 PDD 都经过 AgentRuntime 进入 AgentGraph。
2. `ChatService` 不再承担业务 workflow，`ConsoleRuntime` 不再承担 AI workflow。
3. Guard、Social、Intent、Product、FAQ、RAG、Fallback、Human、Response 的主要
   业务步骤由 Graph Node 表达。
4. 业务分支主要由 Conditional Edge 表达，StatePatch 成为 Node 状态更新机制。
5. Checkpoint 与 Node execution 对齐。
6. Customer Demo 与 PDD 共享相同 Agent Runtime。
7. 旧 orchestrator 已删除或降级为 adapter。
8. 相关测试、Ruff 和 GitNexus change analysis 通过，且没有未解释 HIGH/CRITICAL
   风险。
9. 架构文档与实际实现一致。

详细阶段与验收边界见 `docs/AGENT_GRAPH_MIGRATION.md`。

---

## Git 提交与远程同步规则

本仓库要求 Codex 在每次代码修改任务完成后，自动完成 Git 检查、提交，并同步推送到 Gitee 和 GitHub。

### 1. 基本原则

完成用户要求的代码修改后，不要停留在“修改完成”状态。

默认继续执行以下流程：

```text
代码修改
  ↓
检查 diff
  ↓
运行相关测试
  ↓
运行 lint
  ↓
检查敏感信息
  ↓
git add
  ↓
git commit
  ↓
push Gitee
  ↓
push GitHub
  ↓
报告 commit SHA 和 push 结果
```

除非用户在当前任务中明确要求：

* 不提交
* 不 push
* 只修改本地代码
* 只进行分析

否则默认自动提交并推送。

---

### 2. 当前仓库 Git 约定

当前主分支：

```text
master
```

远程仓库：

```text
origin  = Gitee
github  = GitHub
```

正常情况下，每次完成修改后都需要同步到两个远程：

```bash
git push origin master
git push github master
```

不得只推送其中一个远程后就宣称任务全部完成。

---

### 3. 修改完成后的必做检查

在提交前必须执行：

```bash
git status --short
git diff --check
```

并检查实际修改：

```bash
git diff
git diff --cached
```

必须确认：

1. 修改内容确实属于当前任务；
2. 没有误改无关文件；
3. 没有调试临时代码；
4. 没有意外生成的大文件；
5. 没有本地运行时文件；
6. 没有密钥、Token、Cookie、密码或其他 Secret。

重点禁止提交：

```text
.env
API Key
Access Token
Cookie
Session
浏览器登录数据
SSH Private Key
数据库真实密码
第三方服务 Secret
运行时截图或聊天隐私数据
```

如果发现疑似 Secret：

立即停止自动提交和 push。

明确报告：

```text
BLOCKED: 检测到可能的敏感信息，未执行 commit/push。
```

不要擅自把 Secret 推送到远程。

---

### 4. 测试要求

根据修改范围执行相关测试。

默认优先执行：

```bash
uv run pytest -q
uv run ruff check .
git diff --check
```

如果完整测试代价过高，可以先执行与本次修改直接相关的测试，然后在合理情况下执行完整测试。

例如：

```bash
uv run pytest tests/qa -q
uv run pytest tests/agent -q
uv run pytest tests/unit/test_xxx.py -q
```

代码修改涉及：

* State
* Checkpoint
* Repository
* API
* Database Model
* Migration
* RAG
* Agent

时，应优先运行对应测试。

---

### 5. 测试失败时禁止盲目提交

如果是本次修改造成的测试失败：

```text
不要 commit
不要 push
```

先：

```text
复现
→ 定位根因
→ 最小修改
→ 补回归测试
→ 再次验证
```

只有验证通过后才能提交。

如果失败明确来自外部环境，例如：

* MySQL 未启动
* Milvus 未启动
* Chrome 不存在
* PDD 页面不可访问
* LLM API 不可访问
* 本地模型不存在

并且确认与本次代码修改无关，可以继续提交，但最终报告中必须标记：

```text
BLOCKED / SKIPPED
```

并写明具体原因。

不得把“没有执行”写成“测试通过”。

---

### 6. 提交前再次确认工作区

修改和测试完成后执行：

```bash
git status --short
```

如果：

```text
working tree clean
```

则说明当前任务没有产生需要提交的新修改。

这种情况下：

```text
不要创建空 commit
不要执行无意义 commit
```

可以直接报告：

```text
No changes to commit.
```

如果存在修改，继续下面的提交流程。

---

### 7. 自动暂存

提交当前任务产生的合法修改：

```bash
git add -A
```

然后检查：

```bash
git diff --cached --check
git diff --cached
```

不得未经检查直接 commit。

---

### 8. Commit Message 规范

Commit message 使用：

```text
<type>(<scope>): <description>
```

常用 type：

```text
feat      新功能
fix       Bug 修复
refactor  重构，不改变主要功能
test      测试
docs      文档
chore     工程、配置、仓库整理
perf      性能优化
```

示例：

```text
feat(rag): add retrieval context state
```

```text
fix(agent): preserve checkpoint state on retry
```

```text
refactor(state): introduce typed session state
```

```text
test(rag): add product context filtering cases
```

```text
chore(repo): restore missing tracked modules
```

Commit message 必须描述本次真实修改。

禁止长期使用：

```text
update
fix
修改
test
aaa
```

这种无法表达变更内容的提交信息。

---

### 9. 自动 Commit

完成检查后执行：

```bash
git commit -m "<根据本次任务生成的 commit message>"
```

提交成功后记录：

```bash
git rev-parse --short HEAD
```

保存本次 commit SHA，用于最终报告。

---

### 10. 自动推送 Gitee

首先推送：

```bash
git push origin master
```

如果成功，再继续 GitHub。

如果失败：

不要执行 force push。

先检查失败原因。

常见问题：

```text
认证失败
远程分支领先
网络失败
权限不足
non-fast-forward
```

禁止为了“自动完成任务”直接：

```bash
git push --force
git push -f
```

除非用户当前任务明确授权。

---

### 11. 自动推送 GitHub

Gitee 成功后执行：

```bash
git push github master
```

同样：

禁止自动 force push。

---

### 12. non-fast-forward 处理规则

如果出现：

```text
non-fast-forward
```

不要直接执行：

```bash
git push --force
```

先执行：

```bash
git status
git log --oneline --decorate -10
git fetch <remote>
```

检查：

* 远程是否存在用户的新提交；
* 是否可以安全 rebase；
* 是否存在冲突。

不要自动覆盖远程用户代码。

如果无法安全判断：

停止 push，并在最终报告中说明。

---

### 13. 禁止自动执行的 Git 操作

未经用户明确授权，禁止：

```bash
git push --force
git push -f
git reset --hard
git clean -fd
git clean -fdx
git checkout -- .
git restore .
git rebase --onto
git filter-repo
git filter-branch
```

也禁止：

* 删除用户未提交代码；
* 覆盖用户本地修改；
* 删除远程分支；
* 重写已经推送的 Git 历史。

---

### 14. 用户已有本地修改时

开始任务时先执行：

```bash
git status --short
```

如果存在用户原本就有的未提交修改：

不要擅自删除、覆盖或回滚。

必须区分：

```text
任务开始前已经存在的修改
```

和：

```text
本次 Codex 新产生的修改
```

尽量只修改当前任务需要的文件。

如果现有修改与任务发生冲突：

优先保留用户修改。

---

### 15. 新文件检查

特别注意：

任务中新建的重要文件必须确认已经进入 Git。

执行：

```bash
git status --short
git ls-files <file>
```

避免再次出现：

```text
本地代码能运行
但必要文件没有 commit
GitHub fresh clone 无法启动
```

尤其检查：

```text
app/
tests/
alembic/
scripts/
docs/
```

中新增加的必要文件。

---

### 16. Push 后验证

两个远程 push 完成后执行：

```bash
git status
git log -1 --oneline
git remote -v
```

期望：

```text
working tree clean
```

并确认本地不存在未提交的任务代码。

必要时检查：

```bash
git status -sb
```

确保本地分支没有意外 ahead/behind。

---

### 17. 最终任务报告格式

完成任务后必须明确报告 Git 状态。

使用类似：

```text
Implementation:
- 完成 xxx
- 完成 xxx

Validation:
- pytest: PASS
- ruff: PASS
- diff-check: PASS

Git:
- Branch: master
- Commit: abc1234 feat(rag): add retrieval context
- Gitee: PUSHED
- GitHub: PUSHED
- Working tree: clean
```

如果有测试未执行：

```text
Validation:
- unit tests: PASS
- integration tests: SKIPPED
  Reason: Milvus unavailable
```

如果 push 失败：

```text
Git:
- Commit: abc1234
- Gitee: PUSHED
- GitHub: FAILED
  Reason: authentication failed
```

不得把失败写成成功。

---

### 18. 默认完成定义

对于会修改仓库代码的普通开发任务：

只有完成下面全部步骤，才视为任务完成：

```text
代码完成
+
测试完成
+
lint 完成
+
commit 完成
+
Gitee push 完成
+
GitHub push 完成
```

如果某一步客观无法完成，应明确报告阻塞原因。

---

### 19. 与开发规则的关系

Git 自动提交与推送必须发生在所有开发工作完成之后。

仍然严格遵循：

```text
inspect
→ reproduce
→ root cause
→ minimal change
→ regression test
→ pytest
→ lint
→ diff review
→ commit
→ push
```

禁止为了尽快 commit/push 而跳过前面的代码质量和验证步骤。

---

### 20. Codex 默认行为

以后在本仓库执行用户要求的代码修改任务时：

如果用户没有明确要求“不提交”或“不推送”，则：

**默认在完成并验证修改后，自动提交到当前 master 分支，并依次推送到 `origin` 和 `github`。**

不需要再次询问用户：

```text
是否需要我提交？
是否需要我 push？
```

正常验证通过后直接执行。
