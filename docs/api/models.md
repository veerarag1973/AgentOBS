# tracium.models

Pydantic v2 model layer for tracium events.

Provides `EventModel` (and `TagsModel`) which mirror the `Event` envelope with
strict Pydantic field validation and bidirectional conversion.

**Requires the `pydantic` extra:**

```bash
pip install "tracium[pydantic]"
```

All validation rules are equivalent to `Event.validate()`, so
`EventModel.from_event(event)` succeeds for any event that passes `event.validate()`.

---

## `TagsModel`

```python
class TagsModel(BaseModel)
```

Pydantic model for event tags. Accepts arbitrary `str → str` extra fields.

All values must be strings; non-string values are rejected by Pydantic.
The model is frozen (immutable).

**Example:**

```python
from tracium.models import TagsModel

tags = TagsModel(env="production", model="gpt-4o")
tags.model_dump()  # {"env": "production", "model": "gpt-4o"}
```

### Class methods

#### `from_tags(tags: Tags) -> TagsModel` *(classmethod)*

Construct from a `Tags` instance.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `tags` | `Tags` | A `Tags` instance. |

**Returns:** `TagsModel`

### Methods

#### `to_tags() -> Tags`

Convert back to a `Tags` instance.

**Returns:** `Tags`

---

## `EventModel`

```python
class EventModel(BaseModel)
```

Pydantic v2 model for the tracium event envelope.

The model is frozen (`frozen=True`). Each field carries a Pydantic `Field`
description and is validated by a `@field_validator`. Validation rules are
equivalent to `Event.validate()`.

**Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `schema_version` | `str` | `"2.0"` | Schema version string. |
| `event_id` | `str` | — | 26-character ULID. |
| `event_type` | `str` | — | Namespaced event type. |
| `timestamp` | `str` | — | UTC ISO-8601 timestamp. |
| `source` | `str` | — | Tool name + semver. |
| `payload` | `Dict[str, Any]` | — | Non-empty payload dict. |
| `trace_id` | `str \| None` | `None` | 32-char hex OpenTelemetry trace ID. |
| `span_id` | `str \| None` | `None` | 16-char hex OpenTelemetry span ID. |
| `parent_span_id` | `str \| None` | `None` | 16-char hex parent span ID. |
| `org_id` | `str \| None` | `None` | Organisation identifier. |
| `team_id` | `str \| None` | `None` | Team identifier. |
| `actor_id` | `str \| None` | `None` | Actor identifier. |
| `session_id` | `str \| None` | `None` | Session identifier. |
| `tags` | `TagsModel \| None` | `None` | Arbitrary metadata tags. |
| `checksum` | `str \| None` | `None` | SHA-256 payload checksum. |
| `signature` | `str \| None` | `None` | HMAC-SHA256 audit chain signature. |
| `prev_id` | `str \| None` | `None` | ULID of the preceding event in the chain. |

**Example:**

```python
from tracium.models import EventModel

model = EventModel(
    event_id="01ARYZ3NDEKTSV4RRFFQ69G5FAV",
    event_type="llm.trace.span.completed",
    timestamp="2026-03-01T12:00:00.000000Z",
    source="llm-trace@0.3.1",
    payload={"status": "ok"},
)
json_schema = model.model_json_schema()
```

### Class methods

#### `from_event(event: Event) -> EventModel` *(classmethod)*

Construct an `EventModel` from an `Event` instance.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | A validated or unvalidated `Event`. |

**Returns:** `EventModel` with all fields populated.

**Raises:** `pydantic.ValidationError` — if the event contains invalid field values.

### Methods

#### `to_event() -> Event`

Convert back to an `Event` instance.

Provides lossless round-trip: `EventModel.from_event(event).to_event()` yields
an event with the same field values as the original.

**Returns:** `Event`
