# Rule Engine / Customer Service Rules v1

## 1. Runtime position

### Current

```text
ChatService
    ↓
RuleRegistry
```

Unified Chat currently invokes `RuleRegistry` directly inside
`ChatService._route_chat()`. PDD currently reaches a separate procedural decision
chain through `ConsoleRuntime._evaluate_batch()`. FAQ exact matching currently runs
before rules in Unified Chat; this is the recorded behavior and must not be changed
without the regression baseline required by `docs/AGENT_GRAPH_MIGRATION.md`.

### Target

```text
AgentGraph
    ↓
GuardNode
    ↓
RuleRegistry
    ↓
Conditional Edge
```

`RuleRegistry` remains an independent deterministic capability. The Graph decides
when Guard runs and which path follows the rule result; individual rule patterns must
not be absorbed into Graph Edge logic. High-risk human/complaint/after-sale rules
must become a front Guard before knowledge answer, product generation, and generic
LLM fallback, but only after behavior-preserving tests are in place.

The rule layer is a deterministic safety and context gate in the current unified chat path:

```text
POST /api/v1/chat
        ↓
ChatService
        ↓
RuleRegistry
        ↓
Rule evaluation
        ↓
Terminal?
```

The same deterministic layer is also exposed for diagnostics and compatibility as
`POST /api/v1/rules/evaluate`. That endpoint evaluates rules only; it does not perform product lookup,
QA/RAG retrieval, message sending, or state checkpointing.

It handles messages whose meaning can be decided reliably from normalized text alone, such as an explicit human request, an active complaint, a high-risk after-sale action, a pure greeting, a pure courtesy close, or a message that requires the current product context.

It is not a replacement for Intent or RAG:

- It does not call an LLM, database, Tool, PDD connector, or FastAPI request object.
- It does not send a customer message or perform a transfer.
- It only returns a serializable decision. `ChatService` consumes that decision and decides whether to return a fixed reply, route to human, continue to Product Resolution, or fall through to QA.
- The same message and `RuleContext` always produce the same result.

This keeps low-latency, high-certainty behavior ahead of PostgreSQL product lookup and QA/RAG retrieval.

## 2. Terminal versus Enrichment

| Kind | Behavior | v1 rules |
| --- | --- | --- |
| Terminal | A match resolves this turn before Product Resolution and QA/RAG. `ChatService` returns the fixed reply / route and does not call product answer or QA. | `HumanHandoffRule`, `ComplaintRule`, `AfterSaleRiskRule`, `GreetingRule`, `CourtesyRule` |
| Enrichment | A match adds context but does not resolve the turn. Product Resolution or QA can still run. | `ProductContextRule` |

`RuleRegistry.evaluate()` collects all matched enrichment decisions first, then runs terminal rules by descending priority and stops at the first match. This ordering preserves product context even when a higher-priority risk rule eventually terminates the turn.

## 3. Rules and priority

| Priority | Rule | Terminal | Main result |
| ---: | --- | --- | --- |
| 100 | `HumanHandoffRule` | Yes | `route=human`, `requires_human=true`, `explicit_human_request` |
| 95 | `ComplaintRule` | Yes | `route=human`, `requires_human=true`; complaint/report/platform/rights reason |
| 90 | `AfterSaleRiskRule` | Yes | `route=human`, `requires_human=true`; refund/return/exchange/cancel/compensation reason |
| 60 | `ProductContextRule` | No | `requires_product=true`, `implicit_product_reference` |
| 50 | `GreetingRule` | Yes | `route=greeting`, `simple_greeting`, fixed reply |
| 40 | `CourtesyRule` | Yes | `fallback` route plus `thanks`, `acknowledgement`, or `farewell` reason |

A larger number is evaluated earlier. For terminal rules, the first match wins. Enrichment rules cannot terminate and therefore do not compete with a terminal decision.

### Matching boundaries

The rules intentionally avoid broad substring matches:

- `人工客服和机器人有什么区别？` is a concept question, not a transfer request.
- `支持退款吗？` and `怎么申请退款？` are policy questions, not action requests.
- `这个产品投诉多吗？` discusses complaints but does not initiate one.
- `你好，这个怎么戴？` is not a pure greeting.
- `谢谢，这个一天戴多久？` is not a pure courtesy close.

## 4. RuleDecision

`app.rules.base.RuleDecision` is Pydantic v2, forbids extra fields, and is JSON-serializable:

- `matched`
- `rule_name`
- `terminal`
- `route` (`None` or the existing `app.agent.state.ChatRoute`)
- `reason_code`
- `confidence` in `[0, 1]`
- `fixed_reply`
- `requires_human`
- `requires_product`
- `metadata`

`route` reuses the current State Contract v2 type. The rules do not define another route enum. Enrichment decisions normally leave `route` unset.

## 5. RuleContext

`RuleContext` is deliberately small:

- `session_id`
- `customer_id`
- `shop_id`
- `current_product_id`
- `service_stage` (`pre_sale`, `post_sale`, or `general`, reusing `ServiceStage`)
- `channel`

It does not contain the complete session state, message history, database model, product snapshot, tool instance, or request object. A caller must project only the fields needed by the rule layer.

## 6. Normalization and registry

`RuleRegistry.evaluate(message, context)`:

1. Preserves `raw_message`.
2. Calls `normalize_message`.
3. Executes non-terminal enrichment rules and collects matches.
4. Executes terminal rules by descending priority.
5. Stops after the first terminal match.
6. Returns a `RuleEvaluationResult`.

Normalization performs Unicode NFKC normalization, case folding, whitespace collapsing, stripping, and trailing punctuation cleanup. It preserves digits, order IDs, product models, Chinese body text, and internal punctuation. For example, `"  你好！！  "` becomes `你好`; `"订单 10086-A"` remains semantically intact.

`RuleEvaluationResult` contains:

- `raw_message`
- `normalized_message`
- matched `decisions`
- `terminal_decision`
- aggregate `requires_product`
- aggregate `requires_human`
- merged decision `metadata`

Use `default_rule_registry()` to build a fresh registry containing all v1 rules. `RuleRegistry` can also register custom `BaseRule` implementations; it never specializes behavior by concrete rule class.

## 7. Multi-rule resolution in unified chat

```text
POST /api/v1/chat
      ↓
ChatService
      ↓
Normalize
      ↓
RuleRegistry
      │
      ├── Enrichment: Product Context
      ↓
      ├── Human
      ├── Complaint
      ├── AfterSale Risk
      ├── Greeting
      └── Courtesy
      ↓
Terminal?
 ┌────┴────┐
YES        NO
↓           ↓
Fixed       Product Resolution
Reply /       ↓
Human      Product Answer?
             ↓
          QAService
             ↓
        FAQ / RAG / fallback
```

Examples:

- `你好，我要投诉` — complaint wins over greeting; terminal route is `human`.
- `您好，我要人工` — human handoff wins over greeting; terminal route is `human`.
- `这个不合适，我要退款` — product enrichment remains available while `AfterSaleRiskRule` terminates with `human`.
- `这个怎么戴` — only product enrichment matches; no terminal decision, so `ChatService` can continue to product answer when a bound product exists.
- `谢谢，这个一天戴多久？` — neither courtesy nor greeting matches; the product question remains available.

### Refund action versus refund policy

`AfterSaleRiskRule` intentionally distinguishes execution requests from policy questions:

- `我要退款` means the customer is asking the system to perform or start an after-sale action. It is terminal and routes to human.
- `支持退款吗？` asks for policy information. It is not terminal by this rule and may continue to Product Resolution or QA.
- `怎么申请退款？` is also a policy/process question unless phrased as a direct action request such as `请帮我申请退款`.

Do not replace these patterns with a broad substring match on `退款`; that would incorrectly short-circuit safe policy questions.

## 8. ProductContextRule boundary

`ProductContextRule` is not a product database reader and not a product answer generator. It only marks that the message needs product context when the user asks a product-shaped question and either refers to the current product (`这个`, `这款`, `它`) or `RuleContext.current_product_id` already exists.

Examples:

- `一天戴多久？` with `current_product_id` set requires product context.
- `这个怎么清洗？` requires product context because it contains an implicit product reference.
- A message without a product question or bound product does not match.

The rule does not persist product facts. In unified chat, `ChatService` uses the result to decide whether it should resolve a product ID and then ask PostgreSQL-backed product services for facts.

## 9. Current scope and non-responsibilities

This version intentionally does **not** implement database product detail queries, PostgreSQL repository access, Milvus retrieval, LLM generation, Tool execution, side effects, full Agent State, Checkpoint persistence, or Agent Graph orchestration.

Rule Engine is the deterministic decision layer. Product facts belong to PostgreSQL-backed services, QA/RAG evidence belongs to `QAService` and Milvus/BM25 retrieval, and runtime workflow state belongs to the State Contract / Checkpoint infrastructure.
