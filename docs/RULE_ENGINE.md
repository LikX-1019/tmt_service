# Rule Engine / Customer Service Rules v1

## 1. Why rules run before Intent

The rule layer is a deterministic safety and context gate placed before FAQ, Intent Classifier, and RAG. It handles messages whose meaning can be decided reliably from normalized text alone, such as an explicit human request, an active complaint, a high-risk after-sale action, a pure greeting, or a pure courtesy close.

It is not a replacement for Intent or RAG:

- It does not call an LLM, database, Tool, PDD connector, or FastAPI request object.
- It does not send a customer message or perform a transfer.
- It only returns a serializable decision. The future caller decides which side effect, if any, to execute.
- The same message and `RuleContext` always produce the same result.

This keeps low-latency, high-certainty behavior separate from probabilistic classification and retrieval.

## 2. Terminal versus Enrichment

| Kind | Behavior | v1 rules |
| --- | --- | --- |
| Terminal | A match resolves this turn's rule phase. The caller can use `fixed_reply` and/or route to human without invoking FAQ, Intent, or RAG. | `HumanHandoffRule`, `ComplaintRule`, `AfterSaleRiskRule`, `GreetingRule`, `CourtesyRule` |
| Enrichment | A match adds context but does not resolve the turn. Later FAQ, Intent, or RAG still runs. | `ProductContextRule` |

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

## 7. Multi-rule resolution

```text
Customer Message
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
Fixed       Future FAQ
Reply /       ↓
Human      Intent Classifier
             ↓
         RAG / Tool / General
```

Examples:

- `你好，我要投诉` — complaint wins over greeting; terminal route is `human`.
- `您好，我要人工` — human handoff wins over greeting; terminal route is `human`.
- `这个不合适，我要退款` — product enrichment remains available while `AfterSaleRiskRule` terminates with `human`.
- `这个怎么戴` — only product enrichment matches; no terminal decision, so a future router may continue.
- `谢谢，这个一天戴多久？` — neither courtesy nor greeting matches; the product question remains available.

## 8. Current scope

This version intentionally does **not** implement FAQ matching, FAQ aliases, Intent Classification, RAG changes, Tool Calling, direct Console integration, PDD auto-reply integration, database persistence, or state-contract changes.

The next integration point is `CustomerMessageRouter`. It should execute the deterministic rule layer first and continue with FAQ, Intent Classifier, and then RAG / Tool / General only when no terminal rule has resolved the turn.
