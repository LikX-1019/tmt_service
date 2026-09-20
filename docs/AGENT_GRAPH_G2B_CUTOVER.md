# Agent Graph G2B Unified Chat Cutover

## 1. G2B Result

```text
G2B = COMPLETED
G2 = COMPLETED
G3 = NOT_STARTED
GRAPH_MIGRATION_FREEZE = active
Production takeover = YES for Unified Chat
PDD takeover = NO
```

## 2. Production Path Before

```text
POST /api/v1/chat
  → ChatService.chat
  → ChatStateRuntime.start
  → MySQL list_recent_turns
  → MySQL append customer
  → ChatService._route_chat
  → ChatStateRuntime.complete
  → MySQL append assistant
```

## 3. Production Path After

```text
POST /api/v1/chat
  → ChatService.chat
  → AgentState
  → ChatStateRuntime.start (coarse chat_turn checkpoint)
  → MySQL append customer
  → AgentRuntime.invoke
  → Unified Chat AgentGraph
  → AgentState
  → agent_state_to_chat_response
  → ChatStateRuntime.complete
  → MySQL append assistant
```

Inside the Graph:

```text
START → session_hydrate → faq_exact → guard → social
      → product_resolve → product_load → product_answer / fallback
      → response → END
```

## 4. ChatService Facade Boundary

On the Graph path ChatService now only:

* normalizes request and generates `chat-<uuid>`;
* creates the outer AgentState;
* maps explicit `product_id` into `ProductReference(source="customer_selected")`
  and `turn.product.resolution_source="request"`;
* starts/fails/completes the coarse checkpoint;
* persists customer and assistant messages;
* invokes AgentRuntime;
* calls the single Graph response mapper;
* logs runtime metadata and propagates errors.

It does not evaluate rules, FAQ, social, product, RAG, or fallback on the Graph path.

## 5. Runtime Mode / Rollback

```python
unified_chat_runtime: Literal["graph", "legacy"] = "graph"
```

Environment:

```text
UNIFIED_CHAT_RUNTIME=graph
```

`legacy` is a **TEMPORARY MIGRATION SWITCH**, selected before request execution.
It runs the frozen `_route_chat()` baseline. Automatic Graph-to-Legacy fallback is
forbidden and tested. Legacy is planned for removal in G6.

## 6. Dependency Composition

`get_chat_service()` is the composition root:

```text
shared repositories / services
  ↓
AgentCapabilities
  ↓
build_unified_chat_graph(capabilities)
  ↓
AgentRuntime
  ↓
ChatService
```

Legacy rollback fields and Graph capabilities receive the same composed capability
instances, avoiding duplicate QA indexes, product services, resolvers, fallbacks, and
repositories.

## 7. Request → AgentState Mapping

`ChatRequest.message` becomes the Turn original query; `conversation_id`,
`customer_id`, and `service_stage` hydrate Session/Turn. Explicit
`ChatRequest.product_id` becomes:

```text
ProductReference(product_id, source="customer_selected")
turn.product.resolution_source = "request"
```

`ChatRequest` itself never enters a Graph Node.

## 8. History Ordering

Legacy persisted the current customer before routing, which could make hydration see
the current message as an unanswered history turn. G2B keeps that persistence timing
but makes `SessionHydrateNode` the Graph history owner.

Hydration excludes the final row only when:

```text
assistant is None
AND customer == current original_query
```

Therefore Fallback receives the same bounded pre-execution history as Legacy, while
the current customer message remains persisted exactly once.

## 9. Message Persistence

* Customer: exactly once, before business execution, success or failure.
* Assistant: exactly once, only after successful response mapping.
* SessionHydrateNode reads history only and never appends.
* Graph mode does not pre-read history in ChatService, avoiding double reads.

## 10. Success Lifecycle

`AgentRuntime.invoke()` returns a revalidated AgentState. That returned object is the
only state used for response mapping, completion checkpoint, logging, and node trace.

Final success state has:

```text
status = COMPLETED
current_node = None
completed_nodes includes chat_turn and all executed Graph nodes
node_trace preserves both outer and Graph nodes
```

## 11. Failure Lifecycle

`AgentNodeExecutionError.failed_state` is passed to `ChatStateRuntime.fail`.
If it already contains a Graph failure, the coarse failure checkpoint preserves:

```text
status = FAILED
error.node = actual Graph node
resume_from = actual Graph node
node_trace = Graph trace
```

Business exceptions wrapped by the Graph are unwrapped after checkpoint persistence,
so `LLMInvocationError` and other AppExceptions retain their existing API contract.
Unknown exceptions continue to propagate. No automatic retry or fallback is added.

## 12. Checkpoint Boundary

`ChatStateRuntime` still owns only the coarse `chat_turn` durable checkpoint.

```text
Graph node execution state: available in final/failed AgentState
Node-level durable checkpoint: NOT YET — G3
LangGraph checkpointer: NOT USED
Per-node file save/resume: NOT IMPLEMENTED
```

## 13. API Contract

`POST /api/v1/chat` still returns the same envelope:

```json
{"code":0,"message":"success","data":{}}
```

All `ChatResponse` fields remain unchanged. The API route does not import AgentRuntime,
construct AgentState, or inspect runtime mode.

## 14. Exception Contract

Graph failures preserve `failed_state`, then ChatService unwraps the original
`AppException` after saving the checkpoint. Existing handlers continue to map, for
example:

```text
LLMInvocationError → 502
ProductServiceUnavailableError → existing product service error
Validation errors → 422
```

No provider traceback, prompt, secret, token, cookie, or full user message is exposed.

## 15. Observability

`chat_response_completed` now includes:

```text
agent_runtime = graph | legacy
run_id
completed_node_count
```

It continues to log route/category/product metadata only. It does not log the full
message, prompt, credentials, or provider payload.

## 16. Rollback Procedure

Emergency rollback:

```text
UNIFIED_CHAT_RUNTIME=legacy
```

Then restart the service. Rollback is decided before a request starts. There is no
per-request automatic fallback because Graph and Legacy may each mutate binding state
and invoke LLMs.

## 17. Remaining Migration Debt

```text
MIGRATION DEBT: FAQ/GUARD ORDERING
```

FAQ exact remains before Guard to preserve Legacy behavior. This is compatibility,
not final safety design. `_route_chat()` remains frozen for rollback and is scheduled
for removal in G6.

## 18. G3 Entry Criteria

**G3: READY.**

Evidence:

* Unified Chat default production path is Graph.
* Coarse success/failure checkpoint tests pass.
* Graph failed node state is retained.
* Message writes are exactly once per lifecycle.
* No duplicate side effects or automatic rollback were introduced.
* Legacy rollback is explicit and isolated.
* Full suite is green.

Validation evidence:

```bash
uv run pytest tests/agent -q                        # 110 passed
uv run pytest tests/unit/test_chat_service.py -q    # 30 passed
uv run pytest tests/integration/test_chat_api.py -q # 10 passed
uv run pytest -q                                    # 479 passed
uv run ruff check .                                 # passed
uv sync --frozen                                    # passed
git diff --check                                    # passed
```

GitNexus post-change analysis: 14 files, 39 symbols, risk CRITICAL, 23 affected
processes. The critical blast radius is the intended production cutover: Unified Chat
`chat` and repository flows now enter AgentRuntime, while `ChatStateRuntime.complete`
and `fail` preserve graph lifecycle/failed-node state. There are no PDD file changes.
The legacy branch remains isolated behind `UNIFIED_CHAT_RUNTIME=legacy`, and all
479 tests pass.
