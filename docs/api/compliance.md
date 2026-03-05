# tracium.compliance

Programmatic compliance testing: v1.0 compatibility checks, audit chain
integrity verification, and multi-tenant isolation testing.

All compliance functions can be called directly without pytest.

See the [Compliance User Guide](../user_guide/compliance.md) for usage examples.

---

## Compatibility checker

### `CompatibilityViolation`

```python
@dataclass(frozen=True)
class CompatibilityViolation:
    check_id: str
    rule: str
    detail: str
    event_id: str
```

A single compliance non-conformance found during a check.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `check_id` | `str` | Numeric code, e.g. `"CHK-1"`. |
| `rule` | `str` | Short description of the rule violated. |
| `detail` | `str` | Human-readable description of the specific problem. |
| `event_id` | `str | None` | The `event_id` of the offending event, or `None`. |

---

### `CompatibilityResult`

```python
@dataclass
class CompatibilityResult:
    passed: bool
    events_checked: int
    violations: List[CompatibilityViolation]
```

Result of a compatibility compliance check across a batch of events.

Evaluates as `True` in a boolean context only when `passed=True`.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `passed` | `bool` | `True` only when zero violations are found. |
| `events_checked` | `int` | Number of events that were inspected. |
| `violations` | `List[CompatibilityViolation]` | Full list of violations. |

---

### `test_compatibility(events: Sequence[Event]) -> CompatibilityResult`

Apply the tracium v1.0 compatibility checklist to `events`.

**Checks performed:**

| Check ID | Rule |
|----------|------|
| CHK-1 | Required fields (`schema_version`, `source`, `payload`) are present and non-empty. |
| CHK-2 | `event_type` uses a registered namespace or valid `x.*` custom prefix. |
| CHK-3 | `source` matches the `<service>@<semver>` pattern. |
| CHK-5 | `event_id` is a valid 26-character ULID. |

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `events` | `Sequence[Event]` | One or more `Event` instances to inspect. |

**Returns:** `CompatibilityResult`

**Example:**

```python
from tracium.compliance import test_compatibility

result = test_compatibility(my_events)
if not result:
    for v in result.violations:
        print(f"[{v.check_id}] {v.rule}: {v.detail}")
```

---

## Audit chain integrity

### `ChainIntegrityViolation`

A single chain integrity violation (broken `prev_id` link, tampered signature,
or non-monotonic timestamp).

### `ChainIntegrityResult`

Result of `verify_chain_integrity()`. Evaluates as `True` only when no
violations were found.

**Attributes:** `passed`, `chain_result`, `violations`, `events_verified`, `gaps_detected`.

---

### `verify_chain_integrity(events: Sequence[Event], org_secret: str, *, check_monotonic_timestamps: bool = True) -> ChainIntegrityResult`

Verify the structural integrity of an ordered event chain.

Checks:
- Each event's `prev_id` points to the preceding event's `event_id`.
- Timestamps are monotonically non-decreasing.
- No gaps in the chain.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `events` | `Sequence[Event]` | Ordered list of events (oldest first). |
| `org_secret` | `str` | HMAC key used when the chain was signed. |
| `check_monotonic_timestamps` | `bool` | When `True` (default), also check that timestamps are non-decreasing. |

**Returns:** `ChainIntegrityResult`

---

## Multi-tenant isolation

### `IsolationViolation`

A single isolation violation found during tenant boundary checking.

### `IsolationResult`

Result of `verify_tenant_isolation()` or `verify_events_scoped()`. Evaluates
as `True` only when no violations were found.

**Attributes:** `passed`, `violations`.

---

### `verify_tenant_isolation(group_a: Sequence[Event], group_b: Sequence[Event], *, strict: bool = False) -> IsolationResult`

Verify that events from two tenant groups do not share `org_id` values.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `group_a` | `Sequence[Event]` | First tenant's events. |
| `group_b` | `Sequence[Event]` | Second tenant's events. |
| `strict` | `bool` | When `True`, events with `org_id=None` are also flagged as violations. |

**Returns:** `IsolationResult`

---

### `verify_events_scoped(events: Sequence[Event], *, expected_org_id: str | None = None, expected_team_id: str | None = None) -> IsolationResult`

Verify that all events in `events` carry the expected scope values.

Useful for asserting that a batch of events belongs to a single organisation or team.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `events` | `Sequence[Event]` | Events to check. |
| `expected_org_id` | `str \| None` | Expected organisation ID, or `None` to skip the org check. |
| `expected_team_id` | `str \| None` | Expected team ID, or `None` to skip the team check. |

**Returns:** `IsolationResult`
