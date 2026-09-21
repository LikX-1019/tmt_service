# Agent Graph G7 Architecture Cleanup

## Result

```text
G7 = COMPLETED
G8 = NOT_STARTED
GRAPH_MIGRATION_FREEZE = active
```

G7 is a behavior-preserving cleanup. It does not change business routing, FAQ/Guard
order, AgentState contract, checkpoint semantics, LLM/RAG strategy, AutoReplyPolicy,
database schema, SendWorker, or `_send_job`.

## Cleanup Items

### ChatStateRuntime

Removed migration leftovers that no longer had production callers:

* `ChatStateRuntime.start()`
* `hydrate_product_binding()`
* `clear_product_binding()`
* `record_rule()`
* `record_product_pending()`
* `record_product_resolved()`
* `record_qa()`
* duplicate Product/QA-to-state mapper helpers used only by those recorders
* the unused `coordinator` constructor parameter

`ChatStateRuntime` now only creates request state and records transport completion or
failure. Durable node lifecycle ownership remains with `AgentRuntime` and
`StateCoordinator`.

### AgentRuntime and graph construction

* `AgentRuntime` now requires an explicitly compiled `CompiledStateGraph`.
* Removed the G1 `build_agent_graph()` compatibility constructor.
* The public graph export is `build_unified_chat_graph`.
* Production composition continues to inject capabilities and a durable coordinator
  explicitly; no implicit default runtime path remains.

### ConsoleRuntime and manager naming

* Removed the unused `ConsoleRuntime` `qa_provider` constructor dependency; QA/RAG is
  supplied to the shared Graph through `AgentCapabilities`.
* Renamed `_legacy_single_shop_mode` to `_single_shop_mode`.
* Renamed `ShopRuntimeManager.legacy_runtime()` to `sole_active_runtime()`.
* Renamed `_LegacyRuntimeManager` to `_InjectedRuntimeManager`.
* Updated stale comments without changing API behavior.

### Documentation and configuration

Synchronized current-state documentation in:

* `AGENTS.md`
* `README.md`
* `docs/AGENT_GRAPH_MIGRATION.md`
* `docs/STATE_DESIGN.md`
* `docs/RULE_ENGINE.md`

Historical G0-G6 evidence documents remain unchanged historical records. Current docs
now distinguish shared Graph production paths from historical rollback evidence.

## Architecture Verification

Unified Chat executes only through:

```text
POST /api/v1/chat
  → ChatService facade
  → AgentRuntime
  → shared AgentGraph
```

PDD executes only through:

```text
BrowserMessage
  → ConsoleRuntime channel adapter
  → AgentRuntime
  → shared AgentGraph
```

Verified invariants:

* No `ChatService._route_chat`.
* No `ConsoleRuntime._evaluate_batch_legacy`.
* No `UNIFIED_CHAT_RUNTIME` or `PDD_AGENT_RUNTIME` runtime switch.
* No second business AgentGraph, AgentState, or customer-service AgentRuntime.
* `ChatService` contains no customer-service business workflow.
* `ConsoleRuntime` contains no AI workflow.
* `SendWorker`, `_send_job`, AutoReplyPolicy, persistence, and outbound remain channel-owned.
* Historical `chat_turn` checkpoints still resolve safely to manual handling.

## Full Regression

Validation results:

* Targeted Chat / Agent / PDD / Console / Runtime cleanup regression: 68 passed.
* Targeted Agent / Chat / QA / Rules regression: 238 passed.
* Critical integration / E2E suite covering Unified Chat, PDD, checkpoint/resume,
  product, QA/RAG, rules, human handoff, outbound/send failure, and UNCERTAIN:
  200 passed.
* `uv run pytest -q`: 533 passed, 1 warning.
* `uv run ruff check .`: passed.
* `uv sync --frozen`: passed, 114 packages checked.
* `git diff --check`: passed.

The single warning is the existing Starlette test-client deprecation warning.

## GitNexus Change Analysis

`node .gitnexus/run.cjs detect-changes --scope all --repo .` completed without
`partial` or `truncated`:

```text
Changed files = 26
Changed symbols = 72
Affected processes = 14
Risk = HIGH
```

The HIGH risk is expected because G7 removes dead code from the core state transport
and runtime composition surfaces: `ChatStateRuntime`, `AgentRuntime`, graph exports,
`get_chat_service`, `ChatService.chat`, ConsoleRuntime construction, and
`ShopRuntimeManager._build_runtime`. Affected flows cover Unified Chat composition and
validation, PDD provisioning/runtime construction, connector focus/status/disable, and
the removed Legacy recorder paths.

Coverage consists of the critical integration/E2E suite, full regression, Ruff, frozen
dependency synchronization, and the architecture searches in this document. No
unexplained HIGH flow remains.

## Remaining G8 Blockers

G7 has no unresolved behavior blocker. G8 remains gated on:

1. Final migration acceptance review across both production entries.
2. Explicit acceptance or correction of the preserved FAQ Exact → Guard order.
3. Security and data-safety review.
4. Final documented review of all architecture contracts and GitNexus risk.
5. Completion of the migration acceptance checklist without marking G8-only evidence
   as complete during G7.

`GRAPH_MIGRATION_FREEZE` remains active.
