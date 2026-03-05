# Governance, Consumer Registry & Deprecations

tracium v1.1 adds three complementary safety and lifecycle-management
subsystems:

| Subsystem | Module | Purpose |
|-----------|--------|---------|
| **Consumer Registry** | `tracium.consumer` | Track which tools depend on which schema namespaces |
| **Event Governance** | `tracium.governance` | Block or warn on specific event types via policy |
| **Deprecation Tracking** | `tracium.deprecations` | Register and surface deprecation notices at runtime |

---

## Consumer Registry

Use the consumer registry to declare, at startup, which schema namespaces your
tool reads and at what minimum schema version.  When the installed
`tracium` package cannot satisfy a registered consumer's minimum
version, `assert_compatible()` raises `IncompatibleSchemaError` immediately —
before any events are processed.

### Register your tool

```python
from tracium.consumer import register_consumer, assert_compatible

register_consumer(
    "billing-agent",
    namespaces=("llm.cost.*",),
    schema_version="1.0",
    contact="platform-team@example.com",
)

# Typically called once at application start
assert_compatible()
```

### Compatibility rule

A consumer with `schema_version="X.Y"` is **compatible** with installed version
`"I.J"` if:

- `X == I` (same major — no breaking changes), and
- `Y ≤ J` (consumer may not require newer features than installed)

### Inspect the registry

```python
from tracium.consumer import get_registry

registry = get_registry()

# All consumers
for record in registry.all():
    print(record.tool_name, record.schema_version)

# Consumers for a specific namespace
cost_consumers = registry.by_namespace("llm.cost.*")
```

### Handle incompatibilities

```python
from tracium.consumer import assert_compatible, IncompatibleSchemaError

try:
    assert_compatible()
except IncompatibleSchemaError as exc:
    for tool_name, required in exc.incompatible:
        print(f"{tool_name} requires schema >= {required}")
```

### CLI check

```bash
tracium check-consumers
```

Prints a table of all registered consumers and their compatibility status.

---

## Event Governance

An `EventGovernancePolicy` lets you define — at the application level — which
event types are acceptable, which are deprecated, and any custom validation rules.

### Configure a policy

```python
from tracium.governance import (
    EventGovernancePolicy, GovernanceViolationError, GovernanceWarning,
    set_global_policy, check_event,
)

policy = EventGovernancePolicy(
    blocked_types={"llm.internal.debug", "llm.internal.raw"},
    warn_deprecated={"llm.legacy.trace"},
    strict_unknown=False,
)
set_global_policy(policy)
```

### Check events

```python
import warnings

try:
    with warnings.catch_warnings():
        warnings.simplefilter("error", GovernanceWarning)
        check_event(my_event)
except GovernanceViolationError as exc:
    print(f"Blocked: [{exc.event_type}] {exc.reason}")
except GovernanceWarning as exc:
    print(f"Deprecated: {exc}")
```

### Custom rules

Custom rules are callables `(event: Event) -> str`. Return a non-empty string to
block the event; return an empty string (or `None`) to allow it.

```python
def require_org_id(event):
    if not event.org_id:
        return "org_id is required for all events in multi-tenant mode"
    return ""

policy = EventGovernancePolicy(custom_rules=[require_org_id])
set_global_policy(policy)
```

### Reset to defaults

```python
from tracium.governance import set_global_policy

set_global_policy(None)  # resets to empty / permissive policy
```

---

## Deprecation Tracking

The `DeprecationRegistry` provides a structured way to register deprecated event
types and warn callers when they are used.

### Register a deprecation notice

```python
from tracium.deprecations import mark_deprecated

mark_deprecated(
    "llm.legacy.trace",
    since="1.1.0",
    sunset="2.0.0",
    replacement="llm.trace.span.completed",
    notes="Use llm.trace.* namespace - payload is identical.",
)
```

### Check and warn

```python
from tracium.deprecations import warn_if_deprecated

# Emits stdlib DeprecationWarning if the type is registered
warn_if_deprecated("llm.legacy.trace")
```

### List all deprecations

```python
from tracium.deprecations import list_deprecated

for notice in list_deprecated():
    print(notice.format_message())
```

### CLI list

```bash
tracium list-deprecated
```

### Pre-populated notices

At import time, `tracium.deprecations` pre-populates the global
registry with all entries from `v2_migration_roadmap()`.  This means any event
type on the Phase 9 roadmap will automatically emit `DeprecationWarning` when
passed to `warn_if_deprecated()` — without any configuration by the caller.

---

## Combined pattern

A typical application startup sequence:

```python
import tracium
from tracium.consumer import register_consumer, assert_compatible
from tracium.governance import EventGovernancePolicy, set_global_policy

# 1. Declare dependencies
register_consumer(
    "my-service",
    namespaces=("llm.trace.*", "llm.cost.*"),
    schema_version="1.1",
)

# 2. Assert compatibility
assert_compatible()

# 3. Set policy
set_global_policy(EventGovernancePolicy(
    blocked_types={"llm.internal.debug"},
    strict_unknown=False,
))

# 4. Process events
from tracium.governance import check_event

for event in incoming_events:
    check_event(event)   # raises on violation
    process(event)
```
