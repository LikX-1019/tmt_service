# Agent Graph G2A Unified Chat Parity

## G2A Result

```text
G2A = COMPLETED
G2 = IN_PROGRESS
G2B = NOT_STARTED
GRAPH_MIGRATION_FREEZE = active
Production takeover = NO
```

Unified Chat Legacy Graph Boundary

`POST /api/v1/chat -> ChatService.chat -> ChatService._route_chat` remains the only
production path. `get_chat_service` does not construct AgentRuntime. The legacy
Unified Chat path is now a frozen migration baseline: no new business capability is
allowed there except parity-required fixes.

## Target Unified Chat Graph

```text
START
  -> session_hydrate
  -> faq_exact
  after_faq:
      hit                    -> response
      miss                   -> guard
  after_guard:
      terminal               -> response
      continue               -> social
  after_social:
      hit                    -> response
      continue               -> product_resolve
  after_product_resolve:
      terminal result        -> response
      pending product id     -> product_load
      not required           -> fallback
  after_product_load:
      answer required        -> product_answer
      no answer required     -> fallback
      not found/error        -> response
  fallback / product_answer -> response -> END
```

This intentionally preserves the current Legacy ordering: binding/history hydration,
FAQ exact, then Guard/Rules, Social, Product, and Fallback.

## Node Inventory

| Node | Responsibility | Capabilities reused |
|---|---|---|
| `SessionHydrateNode` | validate contract, load binding, bounded history, product references | ConversationProductRepository, ChatConversationMessageRepository |
| `FAQNode` | exact FAQ lookup only | `QAService.match_exact` |
| `GuardNode` | deterministic terminal/enrichment rule mapping | `RuleRegistry.evaluate` |
| `SocialNode` | social/empathy decision under Legacy gating | `SocialRouter.classify` |
| `ProductResolveNode` | ID/URL/history/semantic/name/candidate resolution decisions | ProductResolver, SemanticProductResolver, ProductRepository |
| `ProductLoadNode` | load product snapshot, bind/clear MySQL binding | ProductRepository, ConversationProductRepository |
| `ProductAnswerNode` | answer a loaded product | ProductAnswerService |
| `FallbackNode` | best-effort RAG context + contextual LLM fallback | QAService retrieval, ContextualFallbackService |
| `ResponseNode` | enforce complete reply invariant and converge | none |

No `LegacyChatNode`, `UnifiedDecisionNode`, or wrapper around `_route_chat` exists.

## Conditional Edge Inventory

| Edge | Route keys |
|---|---|
| `after_faq` | `response`, `guard` |
| `after_guard` | `terminal`, `continue` |
| `after_social` | `response`, `product_resolve` |
| `after_product_resolve` | `terminal_product_result`, `load_product`, `fallback` |
| `after_product_load` | `product_answer`, `fallback`, `response` |

Edges are pure functions. They read the completed node's typed decision and return a
registered route key; they do not query databases, call LLMs/services, or mutate State.

## Dependency Injection

`AgentCapabilities` is a frozen dataclass and the only Graph dependency container.
It holds RuleRegistry, SocialRouter, ProductResolver, SemanticProductResolver,
ProductAnswerService, ContextualFallbackService, QA provider, conversation/message
repositories, and ProductRepository.

It does not own business state, use FastAPI `Depends`, read `Request`, or act as a
service locator. Nodes receive dependencies through constructors. `app/api/dependencies.py`
is unchanged and does not build this container.

## State Extensions

Added typed, serializable Turn/Session state:

* `AgentReplyState`: source, route, answer, product, candidates, resolution source,
  rule name, reason code, confidence, `qa_hit`, and sources.
* `AgentProductCandidateState` and `AgentSourceState`.
* `ConversationProductReferenceState` for bounded named recent product references.
* `RecentTurnState` with a maximum bounded window in `ShortTermMemoryState`.
* Expanded `ProductResolutionState` status and explicit `resolution_source`.

`AgentReplyState` is part of `ChatTurnState`; it is not a second `AgentState`.
`StatePatch.reply` is the only Node response contract. Existing checkpoint JSON reads
successfully because all additions default safely.

## Response Mapping

`app/services/chat_response_mapper.py` maps `AgentState.reply` plus typed product
state to `ChatResponse`. It is a thin API adapter; Graph Nodes do not import
`ChatRequest` or `ChatResponse`.

Mapped fields include answer, source, route, product, product candidates,
`product_resolution`, rule name, reason code, confidence, `qa_hit`, and QA sources.

## Legacy vs Graph Parity Matrix

| Path | Parity evidence |
|---|---|
| FAQ exact | exact core response fields equal |
| FAQ before Guard overlap | `我要退款` configured as FAQ wins in both runtimes |
| Greeting / Courtesy / Human / Complaint / after-sale action | core response and route parity |
| Refund action vs policy question | terminal rule vs continued fallback parity |
| Social expressions | SocialNode path and response parity |
| Explicit request product | load, bind, answer, and resolution source parity |
| Bound follow-up | binding and product answer parity |
| Name exact/unique/candidates | response and binding parity |
| Historical/semantic reference | selected recent product and binding parity |
| Stale binding | MySQL clear, session clear, and response parity |
| Fallback with bound product | loaded product context passed to fallback |
| RAG retrieval failure | references degrade without blocking fallback |
| Fallback needs human | route and HumanState parity |

`tests/agent/test_unified_chat_graph_parity.py` runs Legacy and Graph with separate
but equivalent fake dependencies and compares core response fields plus binding,
human, node, and state contracts.

## FAQ/Guard Ordering Compatibility Debt

```text
MIGRATION DEBT: FAQ/GUARD ORDERING
```

Legacy currently evaluates FAQ exact before Guard. G2A preserves that order and has
an explicit overlap test where `我要退款` is an exact FAQ and therefore bypasses the
terminal after-sale rule. This is compatibility behavior, not the final safety
architecture. Reordering must be a later dedicated characterization and approval task.

## Production Boundary

```text
/api/v1/chat: LEGACY
ChatService._route_chat: ACTIVE / FROZEN
PDD ConsoleRuntime: UNCHANGED / LEGACY
Production takeover: NO
```

No production flag was added. Message persistence remains outside the G2A decision
graph and will be finalized in G2B.

## Known Gaps

* Production does not call the Graph.
* Real MySQL and product API parity is covered by capability contracts, not a live
  dual-write test.
* Graph Node checkpoint persistence remains G3.
* PDD remains entirely outside this Graph.
* No Memory or Tool implementation was added.

## G2B Entry Criteria

**G2B: READY.**

All Unified Chat parity cases are green. Graph response can map completely to
`ChatResponse`; no Legacy early-return path, binding difference, exception contract,
or state schema incompatibility remains. PDD is unaffected and full regression is
green.

Validation evidence:

```bash
uv run pytest tests/agent -q                        # 100 passed
uv run pytest tests/unit/test_chat_service.py -q    # 30 passed
uv run pytest tests/integration/test_chat_api.py -q # 10 passed
uv run pytest -q                                    # 469 passed
uv run ruff check .                                 # passed
uv sync --frozen                                    # passed
git diff --check                                    # passed
```

GitNexus post-change analysis: 24 files, 164 symbols, risk HIGH, six affected flows.
The HIGH blast radius is expected for G2A: the new Graph adapter and Unified Chat
nodes add executable flows that call shared ProductResolver helpers and extend
`AgentState.apply_patch` with optional typed fields. No production entrypoint,
ChatService branch, API dependency, ConsoleRuntime branch, or PDD send path changed.
All 469 tests and the dedicated Legacy-vs-Graph parity suite pass.
