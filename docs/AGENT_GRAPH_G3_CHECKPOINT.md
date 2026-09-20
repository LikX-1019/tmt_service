# Agent Graph G3 Node Checkpoint & Resume

## 1. G3 Result

```text
G3 = COMPLETED
G4 = NOT_STARTED
GRAPH_MIGRATION_FREEZE = active
```

Agent Graph executable: YES. Node durable checkpoint: YES. Resume capability: YES.
Automatic startup recovery: NO. LangGraph checkpointer: NO.

## 2. Before G3 Runtime

```text
AgentRuntime → Graph → GraphNodeAdapter (memory lifecycle)
ChatStateRuntime → coarse chat_turn checkpoint
```

A process interruption after `before:product_load` could not safely guarantee a
durable resume boundary for that node.

## 3. After G3 Runtime

```text
/api/v1/chat
  → ChatService Facade
  → AgentRuntime.invoke
      → StateCoordinator.create (workflow_created)
      → AgentGraph
          → GraphNodeAdapter
          → StateCoordinator.run_node
              → before:<node>
              → Node → StatePatch
              → after:<node> / failed:<node>
  → AgentState
  → ChatResponse
```

Production `get_chat_service()` builds one `FileCheckpointStore`, one
`StateCoordinator`, a checkpoint-aware graph, and an AgentRuntime holding that same
coordinator. `ChatStateRuntime` receives the same store/coordinator.

## 4. Node Lifecycle Ownership

`StateCoordinator` is the sole durable lifecycle owner when present:

```text
begin_node
before checkpoint
Node(deep copied AgentState) → StatePatch
AgentState.apply_patch
complete_node / fail_node
after / failed checkpoint
```

`GraphNodeAdapter` does not duplicate begin/apply/complete/fail in durable mode. It
only wraps the already-persisted failed state in `AgentNodeExecutionError`. The
in-memory `coordinator=None` mode remains available for tests and G1 compatibility.

## 5. Checkpoint Reason Contract

Executed nodes persist:

```text
before:<node>
after:<node>
```

Failed nodes persist:

```text
before:<node>
failed:<node>
```

No checkpoint or trace is manufactured for a node that did not execute. Graph success
finalization saves `workflow_completed`; Legacy saves `after:chat_turn`.

## 6. Revision Semantics

`FileCheckpointStore` remains the optimistic-concurrency authority. Every save
increments `revision`, and the adapter returns the latest coordinator state to
LangGraph, so the next node starts at the persisted revision. A stale save raises
`CheckpointConflictError`; the runtime does not overwrite a newer checkpoint.

## 7. Graph Entry / Resume Router

`START` now uses pure `route_graph_entry(state)` and an explicit allowlist:

```text
session_hydrate, faq_exact, guard, social, product_resolve,
product_load, product_answer, fallback, response
```

The edge reads only `status` / `next_node`. An unknown next node raises instead of
falling back to `session_hydrate`.

## 8. Interrupted Node Recovery

For a checkpoint at:

```text
status=RUNNING
current_node=product_load
resume_from=product_load
reason=before:product_load
```

`StateCoordinator.load_for_resume()` converts it to:

```text
status=READY
current_node=None
next_node=product_load
```

`AgentRuntime.resume(run_id)` starts at that node. Completed nodes are not replayed.
`NodeTrace.attempt` increases from the persisted history.

## 9. Failed Node Recovery

For retryable:

```text
failed:product_answer
error.node=product_answer
resume_from=product_answer
```

`AgentRuntime.resume(run_id)` marks the run READY and executes `product_answer` and
its later graph path. The new trace is attempt 2 while the failed attempt remains
visible.

## 10. PendingAction Safety

Existing semantics remain:

| State | Resume behavior |
|---|---|
| `PREPARED` | normal recoverable state |
| `EXECUTING` | becomes `UNCERTAIN`; `WAITING_MANUAL`; no retry |
| `UNCERTAIN` | `WAITING_MANUAL`; no retry |
| `SUCCEEDED` while node is still current | `WAITING_MANUAL`; no retry |
| `FAILED` | retained by existing state contract |

No Unified Chat read/LLM/product-binding operation was converted into PendingAction.

## 11. Legacy Checkpoint Compatibility

Legacy rollback still owns coarse `chat_turn`. New coordinator logic treats a
G2B-era checkpoint whose target is `chat_turn` as unsafe for automatic graph replay
and converts it to `WAITING_MANUAL`. Old v1 JSON remains readable through the
existing schema migration, and G2-era JSON is read safely.

## 12. Graph vs Legacy Runtime Boundary

```text
Graph mode:
entry=session_hydrate
no virtual chat_turn
StateCoordinator owns nodes
ChatStateRuntime finalizes response/output state

Legacy mode:
entry=chat_turn
coarse begin/after/fail lifecycle remains unchanged
_route_chat remains frozen rollback only
```

## 13. Cleanup Semantics

Default cleanup still deletes only after full success. `cleanup_completed=False`
retains the final completed checkpoint for inspection. Failure checkpoints always
remain for explicit resume.

## 14. Tests

Added dedicated coverage in
`tests/agent/test_graph_checkpoint_resume.py`:

* exact checkpoint reason sequence;
* successful product path before/after nodes;
* FAQ exact early return;
* failed product node before/failed and no after;
* resume only from failed node;
* interrupted fallback resume with attempt 2;
* PendingAction EXECUTING/UNCERTAIN/SUCCEEDED inconsistency;
* checkpoint conflict;
* legacy `chat_turn` manual safety;
* JSON round trip;
* conditional-edge/next-node invariant.

Validation:

```bash
uv run pytest tests/agent -q                  # 122 passed
uv run pytest tests/unit/test_chat_service.py -q # 30 passed
uv run pytest tests/integration/test_chat_api.py -q # 10 passed
PDD/Console/State/Checkpoint subsets          # 58 passed
uv run pytest -q                              # 499 passed
uv run ruff check .                           # passed
uv sync --frozen                              # passed
git diff --check                              # passed
```

GitNexus post-change: 13 files, 36 symbols, risk CRITICAL, 28 affected processes.
This reflects the intended change to shared Unified Chat production checkpoint flow
and `StateCoordinator` resume infrastructure. The business topology and response
contract are unchanged, PDD files are unchanged, and all 499 tests pass.

## 15. Remaining Recovery Debt

```text
RECOVERY DEBT: retry_count/max_retries policy remains undefined for Graph nodes.
No automatic startup/scheduler recovery is implemented.
No public resume API is introduced.
```

These are deliberate G3 boundaries.

## 16. G4 Entry Criteria

**G4: READY.**

Required evidence is present: production Graph stable, per-node checkpoint stable,
actual-node resume stable, no replay of completed nodes, PendingAction safety stable,
legacy rollback isolated, PDD unaffected, and full regression green.
