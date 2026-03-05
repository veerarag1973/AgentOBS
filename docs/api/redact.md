# tracium.redact

PII redaction framework: sensitivity levels, policy-driven field redaction,
and redaction guards.

See the [Redaction User Guide](../user_guide/redaction.md) for full usage examples.

---

## `Sensitivity`

```python
class Sensitivity(str, Enum)
```

Ordered enumeration of sensitivity levels for `Redactable` values.

Supports ordered comparisons (`<`, `<=`, `>`, `>=`) so policies can filter by
minimum sensitivity threshold.

**Members:**

| Member | String value | Description |
|--------|-------------|-------------|
| `Sensitivity.LOW` | `"low"` | Low sensitivity — e.g. environment names. |
| `Sensitivity.MEDIUM` | `"medium"` | Medium sensitivity — e.g. internal identifiers. |
| `Sensitivity.HIGH` | `"high"` | High sensitivity — e.g. internal system data. |
| `Sensitivity.PII` | `"pii"` | Personally Identifiable Information. |
| `Sensitivity.PHI` | `"phi"` | Protected Health Information (strictest). |

**Example:**

```python
from tracium.redact import Sensitivity

assert Sensitivity.PII > Sensitivity.MEDIUM
assert Sensitivity.PHI > Sensitivity.PII
```

---

## `Redactable`

```python
class Redactable(value: Any, sensitivity: Sensitivity, pii_types: FrozenSet[str] = frozenset())
```

A wrapper that marks a payload value as sensitive.

`Redactable` **never** exposes the wrapped value in `__repr__` or `__str__`,
preventing accidental logging of sensitive data.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `value` | `Any` | The sensitive value to wrap. |
| `sensitivity` | `Sensitivity` | Sensitivity level of the wrapped value. |
| `pii_types` | `FrozenSet[str]` | Set of PII type tags (e.g. `{"email", "phone"}`). Defaults to empty set. |

**Example:**

```python
from tracium.redact import Redactable, Sensitivity

field = Redactable("alice@example.com", Sensitivity.PII, frozenset({"email"}))
str(field)           # '<Redactable:pii>'
print(field.reveal()) # alice@example.com
```

### Properties

#### `sensitivity -> Sensitivity`

The sensitivity level of the wrapped value.

#### `pii_types -> FrozenSet[str]`

Set of PII type category tags (e.g. `{"email", "ssn"}`).

### Methods

#### `reveal() -> Any`

Return the underlying sensitive value.

> ⚠️ Use with care — the returned value is the raw sensitive data.

---

## `RedactionResult`

```python
@dataclass(frozen=True)
class RedactionResult:
    event: Event
    redaction_count: int
    redacted_at: str
    redacted_by: str
```

Result returned by `RedactionPolicy.apply()`.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | The new event with sensitive fields replaced by redaction placeholders. |
| `redaction_count` | `int` | Number of payload values that were redacted. |
| `redacted_at` | `str` | UTC ISO-8601 timestamp when redaction was applied. |
| `redacted_by` | `str` | Identifier of the policy that performed the redaction. |

---

## `PIINotRedactedError`

```python
class PIINotRedactedError(count: int, context: str = "")
```

Raised by `assert_redacted()` when PII is still present in an event.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `count` | `int` | Number of unredacted PII/PHI values found. |

---

## `RedactionPolicy`

```python
@dataclass
class RedactionPolicy(
    min_sensitivity: Sensitivity = Sensitivity.PII,
    redacted_by: str = "policy:default",
    replacement_template: str = "[REDACTED:{sensitivity}]",
)
```

Policy that drives which `Redactable` fields are replaced in an event.

All three fields are configurable at construction time.

**Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_sensitivity` | `Sensitivity` | `Sensitivity.PII` | Minimum sensitivity level to redact. Values below this threshold are left as-is. |
| `redacted_by` | `str` | `"policy:default"` | Identifier embedded in `RedactionResult.redacted_by`. |
| `replacement_template` | `str` | `"[REDACTED:{sensitivity}]"` | Template for the replacement string. `{sensitivity}` is substituted with the sensitivity name. |

**Example:**

```python
from tracium.redact import RedactionPolicy, Sensitivity

policy = RedactionPolicy(min_sensitivity=Sensitivity.MEDIUM)
result = policy.apply(event)
print(result.redaction_count)
```

### Methods

#### `apply(event: Event) -> RedactionResult`

Apply this policy to an event and return a new redacted event.

Traverses the event payload and replaces every `Redactable` value whose
`sensitivity >= min_sensitivity` with the formatted `replacement_template`.
The original event is **not** mutated.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | The event to redact. |

**Returns:** `RedactionResult`

---

## Module-level functions

### `contains_pii(event: Event) -> bool`

Return `True` if any payload value is a `Redactable` with
`sensitivity >= Sensitivity.PII`.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | The event to inspect. |

**Returns:** `bool`

---

### `assert_redacted(event: Event, context: str = "") -> None`

Raise `PIINotRedactedError` if the event still contains unredacted PII or PHI.

Use this as a guardrail before exporting events.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | The event to check. |
| `context` | `str` | Optional context string embedded in the exception message. |

**Raises:** `PIINotRedactedError` — if any `Redactable` PII/PHI values remain in the payload.
