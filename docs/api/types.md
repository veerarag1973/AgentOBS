# tracium.types

Namespaced event type registry and custom type validation helpers.

---

## `EventType`

```python
class EventType(str, Enum)
```

Exhaustive registry of all first-party llm-toolkit event types.

`EventType` is a `str` subclass, so values can be compared directly with plain
strings, used as dict keys, and serialised without conversion:

```python
assert EventType.TRACE_SPAN_COMPLETED == "llm.trace.span.completed"
```

Each member also carries `.namespace`, `.tool`, and `.description` properties.

### Properties

#### `namespace -> str`

The `llm.<tool>` namespace prefix for this event type.

```python
EventType.TRACE_SPAN_COMPLETED.namespace  # "llm.trace"
```

#### `tool -> str`

The owning tool's identifier string.

```python
EventType.TRACE_SPAN_COMPLETED.tool  # "llm-trace"
```

#### `description -> str`

A one-line human-readable description of this event type.

### Members

#### `llm.diff.*` — llm-diff

| Member | String value | Description |
|--------|-------------|-------------|
| `DIFF_COMPARISON_STARTED` | `llm.diff.comparison.started` | A diff comparison has been initiated. |
| `DIFF_COMPARISON_COMPLETED` | `llm.diff.comparison.completed` | A diff comparison finished successfully. |
| `DIFF_REPORT_EXPORTED` | `llm.diff.report.exported` | A diff report has been exported to a file or sink. |

#### `llm.prompt.*` — promptlock

| Member | String value | Description |
|--------|-------------|-------------|
| `PROMPT_SAVED` | `llm.prompt.saved` | A prompt version was saved to the registry. |
| `PROMPT_PROMOTED` | `llm.prompt.promoted` | A prompt version was promoted to a higher environment. |
| `PROMPT_ROLLED_BACK` | `llm.prompt.rolled_back` | A prompt was rolled back to a previous version. |
| `PROMPT_APPROVED` | `llm.prompt.approved` | A prompt version approval was recorded. |
| `PROMPT_REJECTED` | `llm.prompt.rejected` | A prompt version was rejected in the review workflow. |

#### `llm.template.*` — promptblock

| Member | String value | Description |
|--------|-------------|-------------|
| `TEMPLATE_RENDERED` | `llm.template.rendered` | A prompt template was rendered with variable substitution. |
| `TEMPLATE_VARIABLE_MISSING` | `llm.template.variable.missing` | A required template variable was absent at render time. |
| `TEMPLATE_VALIDATION_FAILED` | `llm.template.validation.failed` | Template post-render validation did not pass. |

#### `llm.trace.*` — llm-trace

| Member | String value | Description |
|--------|-------------|-------------|
| `TRACE_SPAN_STARTED` | `llm.trace.span.started` | A tracing span was opened. |
| `TRACE_SPAN_COMPLETED` | `llm.trace.span.completed` | A tracing span completed. Primary event consumed by cost/eval/board. |
| `TRACE_TOOL_CALL_STARTED` | `llm.trace.tool_call.started` | A tool call within a span was initiated. |
| `TRACE_TOOL_CALL_COMPLETED` | `llm.trace.tool_call.completed` | A tool call within a span completed. |

#### `llm.cost.*` — llm-cost

| Member | String value | Description |
|--------|-------------|-------------|
| `COST_RECORDED` | `llm.cost.recorded` | Token usage cost was recorded for a span. |
| `COST_BUDGET_THRESHOLD_REACHED` | `llm.cost.budget.threshold_reached` | Cost crossed a configured warning threshold. |
| `COST_BUDGET_EXCEEDED` | `llm.cost.budget.exceeded` | Cost exceeded the hard budget limit. |

#### `llm.eval.*` — evalkit

| Member | String value | Description |
|--------|-------------|-------------|
| `EVAL_SCENARIO_STARTED` | `llm.eval.scenario.started` | An evaluation scenario run has started. |
| `EVAL_SCENARIO_COMPLETED` | `llm.eval.scenario.completed` | An evaluation scenario run has finished. |
| `EVAL_REGRESSION_FAILED` | `llm.eval.regression.failed` | An evaluation run detected a quality regression versus baseline. |

#### `llm.guard.*` — promptguard

| Member | String value | Description |
|--------|-------------|-------------|
| `GUARD_INPUT_SCANNED` | `llm.guard.input.scanned` | An input was scanned by the guard policy. |
| `GUARD_INPUT_BLOCKED` | `llm.guard.input.blocked` | An input was blocked by the guard policy. |
| `GUARD_OUTPUT_FLAGGED` | `llm.guard.output.flagged` | A model output was flagged by the guard policy. |

#### `llm.redact.*` — llm-redact

| Member | String value | Description |
|--------|-------------|-------------|
| `REDACT_PII_DETECTED` | `llm.redact.pii.detected` | PII was detected in a field. |
| `REDACT_PII_REDACTED` | `llm.redact.pii.redacted` | A field was successfully redacted. |
| `REDACT_SCAN_COMPLETED` | `llm.redact.scan.completed` | A PII scan of an event completed. |

#### `llm.fence.*` — llm-fence

| Member | String value | Description |
|--------|-------------|-------------|
| `FENCE_VALIDATION_PASSED` | `llm.fence.validation.passed` | Output-format validation passed. |
| `FENCE_VALIDATION_FAILED` | `llm.fence.validation.failed` | Output-format validation failed. |
| `FENCE_RETRY_TRIGGERED` | `llm.fence.retry.triggered` | A retry was triggered following a fence validation failure. |

#### `llm.audit.*` — AgentOBS

| Member | String value | Description |
|--------|-------------|-------------|
| `AUDIT_CHAIN_STARTED` | `llm.audit.chain.started` | A new tamper-evident audit chain was initialised. |
| `AUDIT_KEY_ROTATED` | `llm.audit.key.rotated` | The HMAC signing key was rotated. |

#### `llm.cache.*` — llm-cache

| Member | String value | Description |
|--------|-------------|-------------|
| `CACHE_HIT` | `llm.cache.hit` | A semantic cache returned a cached result. |
| `CACHE_MISS` | `llm.cache.miss` | A semantic cache lookup returned no result. |

---

## Module-level functions

### `is_registered(event_type: str) -> bool`

Return `True` if `event_type` is a registered first-party `EventType` value.

```python
from tracium.types import is_registered

is_registered("llm.trace.span.completed")  # True
is_registered("x.my-org.custom.event")      # False
```

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | Event type string to look up. |

**Returns:** `bool`

---

### `namespace_of(event_type: str) -> str`

Return the `llm.<tool>` namespace of a registered event type.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | A registered event type string. |

**Returns:** `str` — the namespace prefix (e.g. `"llm.trace"`).

**Raises:** `EventTypeError` — if `event_type` is not a registered first-party type.

---

### `validate_custom(event_type: str) -> None`

Validate a custom (third-party) event type string.

Custom event types must use the `x.<company>.<…>` prefix pattern.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | Custom event type string to validate. |

**Raises:** `EventTypeError` — if `event_type` does not match the `x.*` pattern or is reserved by the first-party registry.

---

### `get_by_value(value: str) -> Optional[EventType]`

Look up an `EventType` by its string value.

Returns `None` instead of raising if the value is not found.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `value` | `str` | Event type string value to look up. |

**Returns:** `EventType | None`

---

## Constants

### `EVENT_TYPE_PATTERN: str`

Regex pattern that all valid event type strings (registered and custom) must match:

```
^(?:llm\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_.]*){1,2}|x\.[a-z][a-z0-9._]*)$
```
- `validate_custom()` — validate a custom `x.<company>.<…>` event type string
- `namespace_of()` — return the namespace prefix of a given event type string
