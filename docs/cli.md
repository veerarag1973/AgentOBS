# Command-Line Interface

AgentOBS ships a command-line tool, `tracium`, for operational tasks.
The entry-point is installed automatically when you `pip install agentobs`.

```bash
tracium --help
```

```text
usage: tracium [-h] <command> ...

tracium command-line utilities

positional arguments:
  <command>
    check-compat      Check a JSON file of events against the v1.0 compatibility checklist
    validate          Validate every event in a JSONL file against the published schema
    audit-chain       Verify HMAC signing chain integrity of events in a JSONL file
    inspect           Pretty-print a single event by event_id from a JSONL file
    stats             Print a summary of events in a JSONL file
    list-deprecated   Print all deprecated event types from the global deprecation registry
    migration-roadmap Print the planned v1 → v2 migration roadmap
    check-consumers   Assert all registered consumers are compatible with the installed schema

options:
  -h, --help          show this help message and exit
```

## `check-compat`

Validate a batch of serialised events against the tracium v1.0 compatibility
checklist (CHK-1 through CHK-5). Useful in CI pipelines, pre-commit hooks,
and onboarding audits for third-party tool authors.

**Usage**

```bash
tracium check-compat EVENTS_JSON
```

`EVENTS_JSON`
: Path to a JSON file containing a top-level array of serialised
`Event` objects (the output of `[evt.to_dict() for evt in events]`).

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | All events passed every compatibility check. |
| `1` | One or more compatibility violations were found (details printed to stdout). |
| `2` | Usage error, file not found, or invalid JSON. |

**Example — passing**

```bash
$ tracium check-compat events.json
OK — 42 event(s) passed all compatibility checks.
```

**Example — violations found**

```bash
$ tracium check-compat events.json
FAIL — 2 violation(s) found in 42 event(s):

  [01JPXXX...] CHK-3 (Source identifier format): source 'MyTool/1.0' does not match ...
  [01JPYYY...] CHK-5 (Event ID is a valid ULID): event_id 'not-a-ulid' is not a valid ULID
```

**Example — generating an events file**

```python
import json
from tracium import Event, EventType

events = [
    Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="my-tool@1.0.0",
        payload={"span_name": "chat"},
    )
    for _ in range(5)
]

with open("events.json", "w") as f:
    json.dump([evt.to_dict() for evt in events], f, indent=2)
```

**Using in CI (GitHub Actions)**

```yaml
- name: Validate event compatibility
  run: |
    python -c "
    import json
    from tracium import Event, EventType
    events = [Event(event_type=EventType.TRACE_SPAN_COMPLETED,
                    source='my-tool@1.0.0', payload={'ok': True})]
    with open('/tmp/events.json', 'w') as f:
        json.dump([e.to_dict() for e in events], f)
    "
    tracium check-compat /tmp/events.json
```

## Compatibility checks

The `check-compat` command applies these checks to every event:

| Check ID | Rule | Details |
|----------|------|---------|
| CHK-1 | Required fields present | `schema_version`, `source`, and `payload` must be non-empty. |
| CHK-2 | Event type is registered or valid custom | Must be a first-party `EventType` value, or pass `validate_custom` (`x.<company>.<…>` format). |
| CHK-3 | Source identifier format | Must match `^[a-z][a-z0-9-]*@\d+\.\d+(\.\d+)?([.-][a-z0-9]+)*$` (e.g. `my-tool@1.2.3`). |
| CHK-5 | Event ID is a valid ULID | `event_id` must be a well-formed 26-character ULID string. |

## Programmatic usage (no CLI required)

The same checks are available directly in Python:

```python
from tracium.compliance import test_compatibility

result = test_compatibility(events)
if not result:
    for v in result.violations:
        print(f"[{v.check_id}] {v.rule}: {v.detail}")
```

See [tracium.compliance](api/compliance.md) for the full compliance API.

---

## `list-deprecated`

Print all deprecation notices from the global `DeprecationRegistry`.

**Usage**

```bash
tracium list-deprecated
```

**Example output**

```
Deprecated event types (4 total):
  llm.cache.evicted → llm.cache.entry_evicted (since 1.1.0, sunset 2.0.0)
  llm.cost.estimate → llm.cost.estimated (since 1.1.0, sunset 2.0.0)
  llm.eval.regression → llm.eval.regression_failed (since 1.1.0, sunset 2.0.0)
  ...
```

The registry is pre-populated at startup with all entries from
`v2_migration_roadmap()`. Additional notices registered at runtime via
`mark_deprecated()` are also included.

---

## `migration-roadmap`

Print the structured Phase 9 v2 migration roadmap.

**Usage**

```bash
tracium migration-roadmap [--json]
```

**Options**

| Option | Description |
|--------|-------------|
| `--json` | Output the roadmap as a JSON array instead of a human-readable table. |

**Example — table output**

```
v2 Migration Roadmap (9 entries)
===================================
llm.cache.evicted
  Since:       1.1.0
  Sunset:      2.0.0
  Policy:      NEXT_MAJOR
  Replacement: llm.cache.entry_evicted
  Notes:       Rename for namespace consistency.

...
```

**Example — JSON output**

```bash
tracium migration-roadmap --json | python -m json.tool
```

---

## `check-consumers`

Print all consumers registered in the global `ConsumerRegistry` and check
their compatibility with the installed schema version.

**Usage**

```bash
tracium check-consumers
```

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | All consumers are compatible. |
| `1` | One or more consumers require a newer schema version. |

**Example output — all compatible**

```
Registered consumers (2 total):
  billing-agent    namespaces=(llm.cost.*,)          requires=1.0  [OK]
  analytics-agent  namespaces=(llm.trace.*, llm.eval.*)  requires=1.1  [OK]

All consumers are compatible with installed schema version 1.1.0.
```

**Example output — incompatible**

```
Registered consumers (1 total):
  future-tool  namespaces=(llm.trace.*,)  requires=2.0  [INCOMPATIBLE]

ERROR: 1 consumer(s) require a schema version not satisfied by 1.1.0.
```

---

## `validate`

Validate every event in a JSONL file against the published v2.0 JSON Schema.
Useful for checking that events emitted by third-party integrations conform to
the canonical schema before ingestion.

**Usage**

```bash
tracium validate EVENTS_JSONL
```

`EVENTS_JSONL`
: Path to a JSONL file (one serialised `Event` JSON object per line).

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | All events are schema-valid. |
| `1` | One or more events failed validation (details printed to stdout). |
| `2` | Usage error, file not found, or malformed JSON. |

**Example — all valid**

```bash
$ tracium validate events.jsonl
OK — 128 event(s) are all schema-valid.
```

**Example — validation errors**

```bash
$ tracium validate events.jsonl
FAIL — 2 event(s) failed schema validation:

  Line 14: missing required field 'source'
  Line 37: 'event_type' value 'foo.bar' is not a registered EventType
```

---

## `audit-chain`

Verify the HMAC-SHA256 signing chain of a JSONL file produced when
`signing_key` was set via `configure()`. Detects tampering, deletions, and
out-of-order events.

The signing secret is read from the `TRACIUM_SIGNING_KEY` environment variable.

**Usage**

```bash
TRACIUM_SIGNING_KEY=my-secret tracium audit-chain EVENTS_JSONL
```

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | Chain is intact — all signatures verify and no gaps detected. |
| `1` | Chain is broken — at least one tampered event or missing link. |
| `2` | Usage error, file not found, or `TRACIUM_SIGNING_KEY` not set. |

**Example — intact chain**

```bash
$ TRACIUM_SIGNING_KEY=secret tracium audit-chain events.jsonl
OK — chain of 50 event(s) is intact. No tampering or gaps detected.
```

**Example — tampered chain**

```bash
FAIL — chain verification failed:
  Event 01JPXXX... signature mismatch (tampered or wrong key)
  Gap detected: event 01JPYYY... has no prev_id link to prior event
```

---

## `inspect`

Look up a single event by its `event_id` in a JSONL file and pretty-print it
as indented JSON. Useful for debugging a specific event without loading the
whole file.

**Usage**

```bash
tracium inspect EVENT_ID EVENTS_JSONL
```

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | Event found and printed. |
| `1` | Event ID not found in file. |
| `2` | Usage error or file not found. |

**Example**

```bash
$ tracium inspect 01JPXXXXXXXXXXXXXXXXXXX events.jsonl
{
  "event_id": "01JPXXXXXXXXXXXXXXXXXXX",
  "schema_version": "2.0",
  "event_type": "llm.trace.span.completed",
  "source": "my-app@1.0.0",
  ...
}
```

---

## `stats`

Print a human-readable summary of all events in a JSONL file: total count,
breakdown by event type, total input/output tokens, estimated cost, and the
timestamp range of the events.

**Usage**

```bash
tracium stats EVENTS_JSONL
```

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | Summary printed successfully. |
| `2` | Usage error or file not found. |

**Example**

```bash
$ tracium stats events.jsonl
Events:  342 total
Types:
  llm.trace.span.completed  : 300
  llm.cost.token_recorded   :  42
Tokens:  input=48 200  output=12 300  total=60 500
Cost:    $0.1820 USD
Range:   2026-03-04T08:00:00Z → 2026-03-04T09:15:33Z
```
