# Agent Graph G5B PDD Production Cutover

## Result

```text
G5 = COMPLETED
G5A = COMPLETED
G5B = COMPLETED
G6 = NOT_STARTED
GRAPH_MIGRATION_FREEZE = active
DEFAULT_PDD_RUNTIME = graph
LEGACY_PDD_RUNTIME = explicit rollback only
```

## Production path

`ShopRuntimeManager` composes a durable `AgentRuntime` over the existing unified
business graph and injects that runtime into every `ConsoleRuntime` it creates:

```text
BrowserMessage
  → ConsoleRuntime
  → PDD Channel Adapter
  → AgentRuntime
  → shared AgentGraph
  → evaluate_pdd_channel_policy / AutoReplyPolicy
  → ReplyDecision / Handoff / OutboundJob
  → SendWorker
  → Connector
```

The default `Settings.pdd_agent_runtime` value is `graph`. Directly constructed
legacy embeddings without composition-root injection remain compatible and use the
legacy adapter; production shop runtimes always receive the injected Graph runtime.
Setting `PDD_AGENT_RUNTIME=legacy` is the short-lived emergency rollback and is
scheduled for removal in G6.

## WIP handling

The pre-existing modifications in `app/core/config.py` and
`app/services/console_runtime.py` were identified as the interrupted G5B attempt.
They were retained and completed in place. They were not reset, restored, or
overwritten. The cutover commit includes both files because their changes are the
requested migration, not unrelated WIP.

## ConsoleRuntime boundary

The Graph path only invokes the adapter and channel policy. It does not perform the
legacy greeting/social/FAQ/product/RAG/handoff orchestration. The following remain
in `ConsoleRuntime`: connector status and `allow_auto`, `ReplyDecision` persistence,
`KnowledgeGap`, human-takeover persistence, `OutboundJob`, the send queue and worker,
`_send_job`, `connector.send_message`, and failed/uncertain delivery handling.

Graph failure is terminal for the batch: it records one suggest-only
`AGENT_GRAPH_UNAVAILABLE` decision and does not call the Legacy path. No Graph code
calls the connector directly.

## Side-effect evidence

The G5B cutover tests assert:

* default manager-created PDD runtimes inject `AgentRuntime` and select `graph`;
* explicit `legacy` selects the old path;
* Graph failure makes zero Legacy calls and creates no outbound job;
* one Graph decision produces exactly one `ReplyDecision`;
* `auto_send` creates one idempotent `OutboundJob`, while `suggest` creates zero;
* `handoff` transitions the conversation to manual exactly once;
* connector delivery remains owned by the unchanged SendWorker path.

G5A parity remains covered by `tests/agent/test_pdd_graph_parity.py`.

## Validation

The cutover validation completed on the migration worktree:

```text
uv run pytest tests/agent/test_pdd_graph_parity.py -q  → 16 passed
uv run pytest tests/agent/test_pdd_graph_cutover.py -q → 6 passed
uv run pytest -q                                      → 528 passed
uv run ruff check .                                   → All checks passed
uv sync --frozen                                      → Checked 114 packages
git diff --check                                      → passed
```

GitNexus `impact` was run before editing for `ConsoleRuntime`, `_evaluate_batch`,
`ShopRuntimeManager`, `_build_runtime`, and `invoke_pdd_agent`. The exact symbol
results were LOW for the ConsoleRuntime class, `_evaluate_batch`, and
`invoke_pdd_agent`; `_build_runtime` reports CRITICAL because the requested
composition change reaches status/focus/provision/start/lifecycle flows. Those
flows are covered by the full regression and multishop manager tests. The required
pre-commit `detect-changes --scope all` and the task-only staged analysis each
completed with 10 files, 54 symbols, 14 affected processes, and HIGH risk;
`_build_runtime` is the intended CRITICAL composition blast radius. No UNKNOWN
result was treated as LOW. The external `AGENTS.md` rules commit was already at
HEAD and is not part of this change.

## Rollback and entry to G6

Rollback is an explicit configuration change to `PDD_AGENT_RUNTIME=legacy`; Graph
exceptions never trigger that rollback automatically. G6 is not started. It may remove
the legacy orchestration and rollback switch only after the migration acceptance
evidence is complete.
