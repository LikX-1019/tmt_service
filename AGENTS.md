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
