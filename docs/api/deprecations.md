# tracium.deprecations

Per-event-type deprecation tracking — register deprecation notices, format
human-readable messages, and emit standard `DeprecationWarning` signals when
deprecated event types are used.

See the [Governance & Consumer Registry](../user_guide/governance.md) user guide
for usage patterns.

---

## `DeprecationNotice`

```python
@dataclass(frozen=True)
class DeprecationNotice:
    event_type: str
    since: str
    sunset: str
    replacement: str | None = None
    notes: str | None = None
```

An immutable record describing when a single event type was deprecated and what
replaces it.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | The deprecated event type string. |
| `since` | `str` | Version in which the type was deprecated (e.g. `"1.1.0"`). |
| `sunset` | `str` | Version in which the type will be removed (e.g. `"2.0.0"`). **Required.** |
| `replacement` | `str \| None` | Suggested replacement event type, or `None` if there is no direct replacement. |
| `notes` | `str \| None` | Free-form migration guidance, or `None`. |

### Methods

#### `format_message() -> str`

Format a human-readable deprecation message.

**Returns:** `str` — multi-sentence description of the deprecation with
replacement and sunset information when available.

**Example:**

```python
notice = DeprecationNotice(
    event_type="llm.legacy.trace",
    since="1.1.0",
    sunset="2.0.0",
    replacement="llm.trace.span.completed",
    notes="Use the trace namespace instead.",
)
print(notice.format_message())
# 'llm.legacy.trace' is deprecated since 1.1.0 and will be removed in 2.0.0.
# Use 'llm.trace.span.completed' instead. Use the trace namespace instead.
```

---

## `DeprecationRegistry`

```python
class DeprecationRegistry
```

Thread-safe registry mapping event type strings to `DeprecationNotice` objects.

### Methods

#### `mark_deprecated(event_type: str, *, since: str, sunset: str, replacement: str | None = None, notes: str | None = None) -> DeprecationNotice`

Register a deprecation notice.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | The event type string being deprecated. |
| `since` | `str` | Version in which it was deprecated. |
| `sunset` | `str` | Version in which it will be removed. |
| `replacement` | `str \| None` | Optional replacement event type. |
| `notes` | `str \| None` | Optional migration guidance. |

**Returns:** `DeprecationNotice` — the registered notice.

---

#### `get(event_type: str) -> Optional[DeprecationNotice]`

Return the notice for `event_type`, or `None` if not deprecated.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | Event type to look up. |

**Returns:** `DeprecationNotice | None`

---

#### `is_deprecated(event_type: str) -> bool`

Return `True` if `event_type` has a registered deprecation notice.

---

#### `warn_if_deprecated(event_type: str) -> None`

Issue a stdlib `DeprecationWarning` if `event_type` is deprecated.

Uses `warnings.warn(..., DeprecationWarning, stacklevel=2)`. No-op if the
type is not deprecated.

> **Note:** This emits stdlib `DeprecationWarning`, not `GovernanceWarning`.
> Python suppresses `DeprecationWarning` by default in production; use
> `python -W all` or enable warnings in pytest to surface them.

---

#### `list_all() -> List[DeprecationNotice]`

Return all registered deprecation notices sorted by `event_type`.

**Returns:** `List[DeprecationNotice]`

---

#### `remove(event_type: str) -> bool`

Remove the deprecation notice for `event_type`. Returns `True` if a notice was removed, `False` if not found.

---

#### `clear() -> None`

Remove all deprecation notices. Useful in tests.

---

## Module-level helpers

A **global registry singleton** is maintained for package-wide deprecation tracking.
The v2 migration roadmap items are pre-populated at import time via
`tracium.migrate.v2_migration_roadmap()`.

### `get_registry() -> DeprecationRegistry`

Return the global `DeprecationRegistry` singleton.

---

### `mark_deprecated(event_type: str, *, since: str, sunset: str, replacement: str | None = None, notes: str | None = None) -> DeprecationNotice`

Register a notice in the global registry.

---

### `get_deprecation_notice(event_type: str) -> Optional[DeprecationNotice]`

Return the notice for `event_type` from the global registry, or `None`.

---

### `warn_if_deprecated(event_type: str) -> None`

Issue `DeprecationWarning` if `event_type` is in the global registry.

---

### `list_deprecated() -> List[DeprecationNotice]`

Return all notices from the global registry sorted by `event_type`.

**Example:**

```python
from tracium.deprecations import (
    mark_deprecated, warn_if_deprecated, list_deprecated, DeprecationNotice,
)

mark_deprecated(
    "llm.legacy.trace",
    since="1.1.0",
    sunset="2.0.0",
    replacement="llm.trace.span.completed",
)

warn_if_deprecated("llm.legacy.trace")   # emits DeprecationWarning

for notice in list_deprecated():
    print(notice.format_message())
```
