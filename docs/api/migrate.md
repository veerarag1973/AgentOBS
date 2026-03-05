# tracium.migrate

Migration helpers for upgrading events from one schema version to the next,
plus the Phase 9 v2 migration roadmap with structured deprecation records.

See the [Migration Guide](../user_guide/migration.md) for background and strategy.

> **Note:** `v1_to_v2()` raises `NotImplementedError` until the v2.0 schema is
> defined. Write the call-site now to receive the working implementation in a
> future release.

---

## `MigrationResult`

```python
@dataclass(frozen=True)
class MigrationResult:
    source_version: str
    target_version: str
    event_id: str
    success: bool
    transformed_fields: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
```

Metadata about a completed migration operation.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `source_version` | `str` | The schema version the event was migrated *from*. |
| `target_version` | `str` | The schema version the event was migrated *to*. |
| `event_id` | `str` | The `event_id` of the event that was transformed. |
| `success` | `bool` | `True` when migration completed without errors. |
| `transformed_fields` | `Tuple[str, ...]` | Names of event fields that were modified. |
| `warnings` | `Tuple[str, ...]` | Any non-fatal issues encountered during migration. |

---

## `SunsetPolicy`

```python
class SunsetPolicy(str, Enum):
    NEXT_MAJOR    = "next_major"
    NEXT_MINOR    = "next_minor"
    LONG_TERM     = "long_term"
    UNSCHEDULED   = "unscheduled"
```

Describes how aggressively a deprecated item will be removed.

| Value | Meaning |
|-------|---------|
| `NEXT_MAJOR` | Removed in the next major release. |
| `NEXT_MINOR` | Removed in the next minor release. |
| `LONG_TERM` | Kept for at least two more major releases. |
| `UNSCHEDULED` | No removal planned; deprecation is advisory only. |

---

## `DeprecationRecord`

```python
@dataclass(frozen=True)
class DeprecationRecord:
    event_type: str
    since: str
    sunset: str
    sunset_policy: SunsetPolicy = SunsetPolicy.NEXT_MAJOR
    replacement: str | None = None
    migration_notes: str | None = None
    field_renames: Dict[str, str] = field(default_factory=dict)
```

Structured deprecation metadata for a single event type on the migration
roadmap.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | The deprecated event type. |
| `since` | `str` | Version in which the type was marked deprecated. |
| `sunset` | `str` | Target version for removal. |
| `sunset_policy` | `SunsetPolicy` | `SunsetPolicy.NEXT_MAJOR` | Removal urgency. |
| `replacement` | `str \| None` | `None` | Recommended replacement event type. |
| `migration_notes` | `str \| None` | `None` | Free-form migration guidance. |
| `field_renames` | `Dict[str, str]` | `{}` | Payload field renames: `{old_name: new_name}`. |

### Methods

#### `summary() -> str`

Return a single-line summary of the deprecation record.

**Example:**

```
llm.eval.regression → llm.eval.regression_failed (since 1.1.0, sunset 2.0.0, NEXT_MAJOR)
```

---

## Module-level functions

### `v2_migration_roadmap() -> List[DeprecationRecord]`

Return the complete list of event types deprecated in v1.1.0 and scheduled for
removal in v2.0.0, sorted by `event_type`.

Each entry documents the recommended replacement, any relevant field renames,
and the `SunsetPolicy` governing its removal timeline.

**Returns:** `List[DeprecationRecord]` — 9 entries covering the `llm.trace.*`,
`llm.eval.*`, `llm.guard.*`, `llm.cost.*`, and `llm.cache.*` namespaces.

**Example:**

```python
from tracium.migrate import v2_migration_roadmap

for record in v2_migration_roadmap():
    print(record.summary())
```

---

### `v1_to_v2(event: Event) -> Tuple[Event, MigrationResult]`

Migrate a v1.0 event to the v2.0 schema.

> **Scaffold only** — raises `NotImplementedError` in v1.x. Write the call-site
> now and upgrade to the full implementation when v2.0 is released. Use
> `v2_migration_roadmap()` to understand which event types will be affected.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | A v1.0 `Event` to migrate. |

**Returns:** `Tuple[Event, MigrationResult]` — `(migrated_event, result)` tuple.

**Raises:** `NotImplementedError` — always in v1.x (v2 schema not yet defined).

**Example:**

```python
from tracium.migrate import v1_to_v2

try:
    new_event, result = v1_to_v2(event)
except NotImplementedError:
    pass  # expected until v2.0 ships
```
