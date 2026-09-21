# Agent Graph G4 Business Hardening

## 1. G4 Result

```text
G4 = COMPLETED
G5 = NOT_STARTED
GRAPH_MIGRATION_FREEZE = active
```

## 2. Before G4 Graph

```text
START → session_hydrate → faq_exact → guard → social
      → product_resolve → product_load / fallback
      → fallback retrieval + generation + human decision
      → response → END
```

Problems: Fallback owned retrieval and generation; human was implicit; core routing
used context strings; product clear semantics were ambiguous; node names were
duplicated.

## 3. After G4 Graph

```text
START / entry router
  → session_hydrate
  → faq_exact
  → guard
  → social
  → product_resolve
  → product_load
  → product_answer / rag_context
  → fallback
  → human_transfer
  → response
  → END
```

Actual paths skip inactive nodes with Conditional Edges.

## 4. RAG Context Separation

`RAGContextNode` only:

1. calls `QAService.retrieve_context_candidates()`;
2. maps `RetrievalDocument` to typed `RetrievalEvidence`, now including `title`;
3. writes `RetrievalState.status = available | empty | unavailable`;
4. routes to `FallbackNode`.

Retrieval remains best-effort: failure records unavailable evidence and continues.

## 5. Fallback Is Pure Generation

`FallbackNode` no longer calls QA retrieval. It reads checkpointed
`RetrievalState.candidates`, converts them back to `RetrievalDocument`, and calls
`ContextualFallbackService.generate()`. A G3/G4 checkpoint after `rag_context` can
resume directly at `fallback` without repeating retrieval.

## 6. Human Transfer Flow

`HumanTransferNode` is explicit and normalizes `HumanState`, ensures
`route=human` and `human.required=true`, and preserves the upstream reply source and
answer. Guard human paths and Fallback needs-human paths both pass through it.

## 7. Typed Routing State

`ProductResolutionState` now carries:

```text
requires_product
product_rule_name
answer_required
```

Social gating, ProductResolve, ProductLoad, ProductAnswer routing, and Response
metadata use these fields. Core graph routing no longer reads the former
`guard_requires_product`, `guard_product_rule_name`, or `product_answer_required`
context keys. Context only retains observability/debug fields such as FAQ/social hit
and rule names.

## 8. Product State Fixes

`StatePatch.clear_current_product` provides explicit clear semantics. ProductLoad
uses it for stale bindings after clearing MySQL, and removes the stale product from
`session.recent_product_ids`. `product_missing` is strongly typed and preserves
`product_rule_name`.

## 9. Channel-neutral Runtime

Guard reads:

```text
state.session.channel
state.session.shop_id
```

instead of hardcoding `unified_chat`. Unified Chat state continues to use its existing
legal channel value (`other` in current runtime), so behavior is unchanged while the
node is reusable for PDD in G5.

## 10. Resume Compatibility

The resumable node registry now includes all active nodes:

```text
session_hydrate, faq_exact, guard, social, product_resolve,
product_load, product_answer, rag_context, fallback,
human_transfer, response
```

Old G3 checkpoints targeting `fallback` resume directly to Fallback; they are not
forced through the new `rag_context` node. New runs naturally traverse
`product_resolve → rag_context → fallback`.

## 11. Topology Registry

`app/agent/constants.py` is the single source for active node names and
`RESUMABLE_GRAPH_NODES`. The entry router imports this registry; the graph builder
uses the same constants. A dedicated test proves exact equality with the compiled
graph's durable nodes.

## 12. Subgraph Decision

Product and Knowledge are logical subgraph candidates, but physical nested LangGraph
subgraphs are **NOT YET** introduced. Root-level durable node identity and
`START → failed/interrupted node` resume remain stable and are not hidden.

## 13. Legacy Boundary

`ChatService._route_chat()` remains frozen temporary rollback. It still uses coarse
`chat_turn`; Graph mode does not. No business logic was added to Legacy.

## 14. Tests

Added `tests/agent/test_graph_g4_hardening.py` covering topology/registry equality,
RAG evidence round trip, no repeated retrieval on Fallback resume, explicit Human
Transfer, product missing contract and typed metadata, stale product clear, and
core routing without magic context.

Existing G2A parity, G2B cutover, checkpoint/resume, API, rules, QA, product, and PDD
tests remain green.

## 15. Remaining Debt

FAQ-before-Guard remains documented compatibility debt. Core routing context fields
such as hit/debug names remain for observability and older checkpoint compatibility.
`IntentNode`, Tool, and Memory remain future extension points rather than fake nodes.

## 16. G5 Entry Criteria

**G5: READY.** Unified Chat business graph is explicit, typed, channel-neutral,
durable, and reusable through `AgentCapabilities`; recovery supports all active nodes;
Legacy rollback and PDD remain isolated.
