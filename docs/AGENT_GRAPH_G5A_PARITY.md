# Agent Graph G5A PDD Parity

## Result

```text
G5 = COMPLETED
G5A = COMPLETED
G5B = COMPLETED
GRAPH_MIGRATION_FREEZE = active
PRODUCTION_CUTOVER = YES
```

## Adapter And Graph Reuse

`app/agent/channels/pdd_adapter.py` maps the latest five `BrowserMessage` values plus
the Console conversation and shop into the existing `AgentState` contract. It sets
`channel=pdd`, `shop_id`, `customer_id`, `conversation_id`, the batch query, and the
current product reference/name without writing `chat_conversation_products`.

PDD can explicitly invoke the same `AgentRuntime` and compiled AgentGraph used by
Unified Chat. The shared graph adds coarse PDD nodes for the Legacy-specific ordering:

```text
session_hydrate -> pdd_handoff -> pdd_greeting -> social -> faq_exact
                -> pdd_intent -> product_* / pdd_rag -> response
```

Product loading and answering continue through the shared Product nodes and existing
capabilities. No node calls or wraps `ConsoleRuntime._evaluate_batch()`.

## Channel Policy Boundary

The Graph decides the answer and whether human handling is required. The PDD adapter
maps typed Graph evidence back to `AutoReplyPolicy` and the existing product answer
gate. The Graph does not persist `ReplyDecision`, create `OutboundJob`, run the send
worker, call `connector.send_message`, inspect connector status, or persist human
takeover state.

## Parity Evidence

`tests/agent/test_pdd_graph_parity.py` executes both real Legacy
`ConsoleRuntime._evaluate_batch()` and the shared AgentGraph against identical fake
capabilities, then compares route, answer, human requirement, product, reason,
confidence, and policy input/output. Coverage includes greeting, social,
complaint/refund handoff, explicit-human current behavior, FAQ, product, missing
product context, product lookup/answer failure, RAG, insufficient knowledge,
fallback, unavailable knowledge, and channel-policy isolation.

G5A deliberately preserves the current PDD behavior where the Legacy router does not
route a plain explicit-human request through its pre-FAQ handoff predicate. Changing
that order is not part of behavior-preserving G5A.

## G5B Follow-up

G5B connected the PDD production adapter to AgentRuntime while keeping AutoReplyPolicy,
decision persistence, outbound jobs, send workers, connector status, and human-takeover
persistence in ConsoleRuntime. The implementation and side-effect evidence are recorded
in `docs/AGENT_GRAPH_G5B_CUTOVER.md`.
