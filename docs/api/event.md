# agentobs.event

Core event envelope and tag container for AgentOBS.

This module provides the `Event` class (the immutable event envelope) and the
`Tags` class (an immutable `str → str` mapping for arbitrary metadata).

See the [Events User Guide](../user_guide/events.md) for full usage examples.

---

## `Tags`

```python
class Tags(**kwargs: str)
```

An immutable, validated `str → str` mapping attached to an event.

All keys and values must be non-empty strings. `Tags` is frozen after
construction — there are no mutation methods.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `**kwargs` | `str` | Arbitrary key-value string pairs. Both key and value must be non-empty strings. |

**Raises:** `SchemaValidationError` — if any key or value is not a non-empty string.

**Example:**

```python
tags = Tags(env="production", model="gpt-4o")
tags.get("env")       # "production"
tags.to_dict()        # {"env": "production", "model": "gpt-4o"}
```

### Methods

#### `get(key: str, default: Optional[str] = None) -> Optional[str]`

Return the value for *key*, or *default* if the key is absent.

#### `keys() -> KeysView[str]`

Return a view of all tag keys.

#### `values() -> ValuesView[str]`

Return a view of all tag values.

#### `items() -> ItemsView[str, str]`

Return a view of all `(key, value)` pairs.

#### `to_dict() -> Dict[str, str]`

Return a plain `dict` copy of the tags.

---

## `Event`

```python
class Event(
    *,
    event_type: str,
    source: str,
    payload: Dict[str, Any],
    schema_version: str = "2.0",
    event_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
    org_id: Optional[str] = None,
    team_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[Tags] = None,
    checksum: Optional[str] = None,
    signature: Optional[str] = None,
    prev_id: Optional[str] = None,
)
```

Immutable, validated event envelope.

All fields are read-only properties after construction. `event_id` and
`timestamp` are auto-generated (ULID and UTC ISO-8601 respectively) if not
provided.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `event_type` | `str` | — | Event type string. Must be either a registered first-party `EventType` value (RFC Appendix B) or a valid reverse-domain custom type outside `llm.*` (e.g. `x.company.entity.action`). |
| `source` | `str` | — | Tool name + full semver, e.g. `"llm-trace@0.3.1"`. |
| `payload` | `Dict[str, Any]` | — | Non-empty dict of event-type-specific data. |
| `schema_version` | `str` | `"2.0"` | Schema version. Allowed values: `"1.0"` and `"2.0"` only. |
| `event_id` | `str \| None` | `None` | 26-character ULID. Auto-generated if `None`. |
| `timestamp` | `str \| None` | `None` | UTC ISO-8601 timestamp. Auto-generated if `None`. |
| `trace_id` | `str \| None` | `None` | OpenTelemetry trace ID — 32 lowercase hex chars. |
| `span_id` | `str \| None` | `None` | OpenTelemetry span ID — 16 lowercase hex chars. |
| `parent_span_id` | `str \| None` | `None` | Parent span ID — 16 lowercase hex chars. |
| `org_id` | `str \| None` | `None` | Organisation identifier. |
| `team_id` | `str \| None` | `None` | Team identifier within the organisation. |
| `actor_id` | `str \| None` | `None` | User or service actor identifier. |
| `session_id` | `str \| None` | `None` | Session or conversation identifier. |
| `tags` | `Tags \| None` | `None` | Arbitrary string key-value metadata. |
| `checksum` | `str \| None` | `None` | SHA-256 payload checksum (set by `signing.sign()`). |
| `signature` | `str \| None` | `None` | HMAC-SHA256 audit chain signature (set by `signing.sign()`). |
| `prev_id` | `str \| None` | `None` | ULID of the preceding event in the audit chain. |

**Raises:** `SchemaValidationError` — if any supplied field value is invalid.

**Example:**

```python
from agentobs import Event, EventType

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="llm-trace@0.3.1",
    payload={"span_name": "run", "status": "ok"},
    org_id="acme",
    tags=Tags(env="production"),
)
```

### Properties

All properties are read-only.

| Property | Type | Description |
|----------|------|-------------|
| `schema_version` | `str` | Schema version string (`"1.0"` or `"2.0"`). |
| `event_id` | `str` | 26-character ULID event identifier. |
| `event_type` | `str` | Namespaced event type string. |
| `timestamp` | `str` | UTC ISO-8601 timestamp string. |
| `source` | `str` | Source tool and version string. |
| `payload` | `Dict[str, Any]` | Read-only mapping view (`MappingProxyType`) of the payload. |
| `trace_id` | `str \| None` | 32-char hex OpenTelemetry trace ID. |
| `span_id` | `str \| None` | 16-char hex OpenTelemetry span ID. |
| `parent_span_id` | `str \| None` | 16-char hex parent span ID. |
| `org_id` | `str \| None` | Organisation identifier. |
| `team_id` | `str \| None` | Team identifier. |
| `actor_id` | `str \| None` | Actor identifier. |
| `session_id` | `str \| None` | Session identifier. |
| `tags` | `Tags \| None` | Immutable tag mapping. |
| `checksum` | `str \| None` | SHA-256 payload checksum. |
| `signature` | `str \| None` | HMAC-SHA256 audit signature. |
| `prev_id` | `str \| None` | ULID of the previous event in the chain. |

### Methods

#### `validate() -> None`

Validate all fields against the schema rules.

Checks every field in order: `schema_version`, `event_id`, `event_type`,
`timestamp`, `source`, `payload`, optional tracing IDs, optional context
strings, optional integrity fields, and tags.

**Raises:** `SchemaValidationError` — on the first invalid field, with `.field`,
`.received`, and `.reason` attributes describing the problem.

---

#### `to_dict(*, omit_none: bool = True) -> Dict[str, Any]`

Serialise to a plain Python dictionary.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `omit_none` | `bool` | `True` | When `True`, fields with `None` values are excluded from the result. |

**Returns:** `Dict[str, Any]` — dictionary representation of the event.

---

#### `to_json() -> str`

Serialise to a canonical, deterministic JSON string.

- Keys are sorted alphabetically at every nesting level.
- `None` values are omitted.
- Uses compact separators (no whitespace).
- Byte-for-byte identical for the same event on any platform.

**Returns:** `str` — compact canonical JSON string.

**Raises:** `SerializationError` — if the payload contains a non-JSON-serialisable value.

---

#### `payload_checksum() -> str`

Compute the SHA-256 digest of the canonical JSON payload.

**Returns:** `str` — hex-encoded SHA-256 prefixed with `"sha256:"`.

---

#### `from_dict(data: Dict[str, Any], *, max_size_bytes: int = 1_048_576, max_payload_depth: int = 10, max_tags: int = 50, source_hint: str = "<dict>") -> Event` *(classmethod)*

Construct an `Event` from a plain dictionary.

The dictionary shape matches `to_dict()` output. The returned event is **not
yet validated** — call `validate()` separately if needed.

DoS limits are enforced before any field parsing (RFC §19.4). Pass `0` to
disable an individual limit.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `Dict[str, Any]` | — | Dictionary with event fields. |
| `max_size_bytes` | `int` | `1_048_576` | Maximum serialised size in bytes (1 MiB). |
| `max_payload_depth` | `int` | `10` | Maximum nesting depth of the `payload` object. |
| `max_tags` | `int` | `50` | Maximum number of keys in the `tags` object. |
| `source_hint` | `str` | `"<dict>"` | Short label used in error messages (e.g. a filename). |

**Returns:** `Event`

**Raises:** `DeserializationError` — if a required field is missing, has an unexpected type, or any DoS limit is exceeded.

---

#### `from_json(json_str: str, *, max_size_bytes: int = 1_048_576, max_payload_depth: int = 10, max_tags: int = 50, source_hint: str = "<json>") -> Event` *(classmethod)*

Construct an `Event` from a JSON string (as produced by `to_json()`).

The returned event is **not yet validated** — call `validate()` if needed.

The byte-length of the UTF-8-encoded string is checked **before parsing** to
guard against parse-bomb attacks (RFC §19.4). Pass `max_size_bytes=0` to
disable the pre-parse check.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `json_str` | `str` | — | A JSON string in the format produced by `to_json()`. |
| `max_size_bytes` | `int` | `1_048_576` | Maximum string size in UTF-8 bytes (1 MiB). |
| `max_payload_depth` | `int` | `10` | Maximum nesting depth of the `payload` object. |
| `max_tags` | `int` | `50` | Maximum number of keys in the `tags` object. |
| `source_hint` | `str` | `"<json>"` | Short label used in error messages. |

**Returns:** `Event`

**Raises:** `DeserializationError` — if `json_str` is not valid JSON, is missing required fields, or any DoS limit is exceeded.
