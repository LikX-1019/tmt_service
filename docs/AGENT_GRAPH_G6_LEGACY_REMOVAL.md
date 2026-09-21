# Agent Graph G6 Legacy Removal

## Result

```text
G6 = COMPLETED
G7 = NOT_STARTED
GRAPH_MIGRATION_FREEZE = active
LEGACY_AI_PATHS = removed
ROLLBACK = git revert
```

## Removed Legacy Paths

G6 removed the duplicate business orchestration paths:

* `ChatService._route_chat()`
* `ConsoleRuntime._evaluate_batch_legacy()`
* `UNIFIED_CHAT_RUNTIME` and `Settings.unified_chat_runtime`
* `PDD_AGENT_RUNTIME` and `Settings.pdd_agent_runtime`
* The former graph/legacy dispatch wrappers in production composition

There is no second runnable customer-service AI path and no runtime switch that can
select one.

## Runtime Boundary

Unified Chat now executes only through:

```text
POST /api/v1/chat
  → ChatService facade
  → AgentRuntime
  → Unified Chat AgentGraph
  → response mapping / persistence
```

PDD now executes only through:

```text
BrowserMessage
  → ConsoleRuntime
  → PDD Channel Adapter
  → AgentRuntime
  → shared AgentGraph
  → channel policy / persistence / outbound
```

`ChatService` is a transport and response facade. `ConsoleRuntime` retains channel,
connector, persistence, queue, and outbound lifecycle responsibilities only.

## Preserved Channel Responsibilities

The following were not moved into the Graph and retain their existing boundaries:

* `SendWorker`
* `ConsoleRuntime._send_job`
* `AutoReplyPolicy`
* conversation, decision, human-takeover, knowledge-gap, and outbound persistence
* `OutboundJob` creation and send-queue dispatch
* connector status, delivery verification, retry, and uncertain-result handling

## Checkpoint Compatibility

Historical virtual `chat_turn` checkpoints remain readable. Resume does not attempt to
execute the removed legacy node; it safely resolves the run to manual handling. Current
Graph runs continue to use the durable node lifecycle and `StatePatch` contract.

## Regression Fix

The interrupted G6 removal left `QAAnswerGenerator` importing `_content_to_text` from
`ChatService`. The LangChain string and text-block normalization helper now lives in
`app/qa/answer_generator.py`, removing the QA → ChatService dependency without
restoring the legacy helper. String, text-block, and empty-content behavior is covered
by QA tests.

## Validation

Validation results:

* `uv run pytest tests/qa -q`: 31 passed.
* `uv run pytest tests/agent tests/integration/test_chat_api.py tests/api/test_qa_api.py tests/unit/test_chat_service.py -q`: 205 passed.
* PDD / Console / Runtime targeted regression: 50 passed.
* `uv run pytest -q`: 534 passed, 1 warning.
* `uv run ruff check .`: passed.
* `uv sync --frozen`: passed, 114 packages checked.
* `git diff --check`: passed.
* GitNexus `detect-changes --scope all`: complete, 25 files, 84 symbols,
  22 affected processes, risk `CRITICAL`.

The GitNexus risk is expected for G6 because the change removes orchestration from the
core ChatService, ChatStateRuntime, ConsoleRuntime, and ShopRuntimeManager composition
paths. The affected flows are the Unified Chat composition, PDD provisioning/runtime
construction, `_on_message` processing, state completion/checkpoint mapping, and QA
generation. Full regression covers those flows through Agent graph, checkpoint, Chat API,
PDD/Console integration, routing, persistence/outbound behavior, and QA tests. No
`partial` or `truncated` result was returned, and no unexplained flow remains outside
that coverage.

## Rollback

G6 rollback is a Git revert of the G6 commit. No runtime feature switch is retained.

## G7 Entry

```text
G7 = NOT_STARTED
```

G7 architecture cleanup has not begun. `GRAPH_MIGRATION_FREEZE` remains active.
