# Events

The `Event` class is the central object in Tracium.
Every interaction between an LLM tool and the outside world — a trace span,
a cost record, a prompt save, a guard block — is represented as an Event.

## The event envelope

Every event carries a common **envelope** regardless of the event type:

| Field | Required | Description |
|-------|----------|-------------|
| `schema_version` | Auto | Defaults to `"2.0"` (consumers also accept `"1.0"` for backward compatibility). |
| `event_id` | Auto | ULID (26-character, time-ordered, URL-safe). Auto-generated if omitted. |
| `event_type` | **Yes** | Namespaced event type string (`llm.<ns>.<entity>.<action>`). Use an `EventType` member or a valid custom `x.*` type. |
| `timestamp` | Auto | UTC ISO-8601 (`YYYY-MM-DDTHH:MM:SS.ffffffZ`). Auto-generated. |
| `source` | **Yes** | Emitting tool in `"name@semver"` format, e.g. `"llm-trace@1.0.0"`. |
| `payload` | **Yes** | Tool-specific `dict`. Must be non-empty. All values must be JSON-serialisable. |
| `trace_id` | Optional | 32-char lowercase hex OpenTelemetry trace ID. |
| `span_id` | Optional | 16-char lowercase hex OpenTelemetry span ID. |
| `parent_span_id` | Optional | 16-char lowercase hex parent span ID. |
| `org_id` | Optional | Organisation identifier for multi-tenant deployments. |
| `team_id` | Optional | Team identifier. |
| `actor_id` | Optional | User or service-account identifier. |
| `session_id` | Optional | Session identifier grouping related events. |
| `tags` | Optional | Arbitrary `str → str` metadata via `Tags`. |
| `checksum` | Signing | SHA-256 payload checksum. Set by `sign`. |
| `signature` | Signing | HMAC-SHA256 chain signature. Set by `sign`. |
| `prev_id` | Signing | `event_id` of the preceding event in the audit chain. |

`Event` is an immutable envelope class with read-only properties after construction.

## Event types

All first-party event types are members of the `EventType` enum:

```python
from tracium import EventType

# Trace namespace
EventType.TRACE_SPAN_COMPLETED       # "llm.trace.span.completed"
EventType.TRACE_SPAN_FAILED          # "llm.trace.span.failed"

# Cost namespace
EventType.COST_TOKEN_RECORDED        # "llm.cost.token.recorded"
EventType.COST_SESSION_RECORDED      # "llm.cost.session.recorded"

# Guard namespace
EventType.GUARD_INPUT_BLOCKED        # "llm.guard.input.blocked"
EventType.GUARD_OUTPUT_FLAGGED       # "llm.guard.output.flagged"

# ... and 40+ more
```

To use your own event types, prefix with `x.<company>`:

```python
event = Event(
    event_type="x.mycompany.pipeline.completed",
    source="my-tool@1.0.0",
    payload={"result": "ok"},
)
```

## Serialisation

Events serialise to and from plain Python dicts and JSON strings:

```python
# dict (omits None fields by default)
d = event.to_dict()
d = event.to_dict(omit_none=False)   # include all fields

# JSON string
s = event.to_json()

# round-trip
event2 = Event.from_dict(d)
event3 = Event.from_json(s)
assert event == event2 == event3
```

## Validation

`Event.validate()` raises `SchemaValidationError` on the first invalid field:

```python
event.validate()   # silent on success; raises on first error
```

For full JSON Schema validation (requires `pip install "tracium[jsonschema]"`):

```python
from tracium.validate import validate_event
validate_event(event)
```

## Pydantic integration

For tools that use Pydantic v2 (requires `pip install "tracium[pydantic]"`):

```python
from tracium.models import EventModel

model = EventModel.from_event(event)
schema = model.model_json_schema()
event_back = model.to_event()
```

## ULIDs

Event IDs use the [ULID](https://github.com/ulid/spec) format — 26-character,
URL-safe, time-ordered, sortable unique identifiers:

```python
from tracium.ulid import generate, validate, extract_timestamp_ms

ulid = generate()                         # "01JPXXXXXXXXXXXXXXXXXXXXXXXX"
assert validate(ulid) is True
ts_ms = extract_timestamp_ms(ulid)        # milliseconds since epoch
```
