# agentobs.validate

JSON Schema validation for `Event` envelopes.

Validates `Event` instances against the published JSON Schema. Schema version
is selected automatically from the event's `schema_version` field:

- `"1.0"` → `schemas/v1.0/schema.json`
- `"2.0"` (default) → `schemas/v2.0/schema.json`

When the optional `jsonschema` package is installed, full Draft 2020-12
validation is performed. Otherwise a stdlib-only structural check covers all
required fields, types, and regex patterns.

**Install for full validation:**

```bash
pip install "agentobs[jsonschema]"
```

---

## Module-level functions

### `validate_event(event: Event) -> None`

Validate `event` against the published JSON Schema.

The schema version is read from `event.schema_version` and the matching schema
file is selected automatically (RFC §15.5).

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | The `Event` instance to validate. |

**Raises:**

- `SchemaValidationError` — if the event does not conform to the envelope schema.
- `FileNotFoundError` — if the matching schema file is missing from the distribution.
- `TypeError` — if `event` is not an `Event` instance.

**Example:**

```python
from agentobs import Event, EventType
from agentobs.validate import validate_event

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="llm-trace@0.3.1",
    payload={"span_name": "run", "status": "ok"},
)
validate_event(event)  # passes silently
```

---

### `load_schema(version: Optional[str] = None) -> Dict[str, Any]`

Load and cache a JSON Schema from disk by version.

The schema is loaded once per version key and cached in memory. If `version`
is `None`, the current default (`"2.0"`) is used. Unknown versions raise
`ValueError`.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `version` | `str \| None` | `None` | Schema version string, e.g. `"1.0"` or `"2.0"`. Defaults to `"2.0"`. |

**Returns:** `Dict[str, Any]` — parsed JSON Schema as a plain Python dict.

**Raises:**

- `FileNotFoundError` — if the schema file cannot be found relative to the package root.
- `ValueError` — if an unknown schema version is requested.

**Example:**

```python
from agentobs.validate import load_schema

schema_v2 = load_schema()        # loads schemas/v2.0/schema.json
schema_v1 = load_schema("1.0")   # loads schemas/v1.0/schema.json
```

---

## Validation rules

| Field | Rule |
|-------|------|
| `schema_version` | Required. Must be one of `"1.0"` or `"2.0"`. |
| `event_id` | Required. Must be a valid 26-character ULID. |
| `event_type` | Required. Must be either a registered first-party RFC event type or a valid reverse-domain custom type outside `llm.*`. |
| `timestamp` | Required. Must be UTC ISO-8601 ending in `Z`. |
| `source` | Required. Must match `tool-name@semver` pattern. |
| `payload` | Required. Must be a non-empty object. |
| `trace_id` | Optional. Must be exactly 32 lowercase hex characters. |
| `span_id` | Optional. Must be exactly 16 lowercase hex characters. |
| `parent_span_id` | Optional. Must be exactly 16 lowercase hex characters. |
| `org_id`, `team_id`, `actor_id`, `session_id` | Optional. Must be non-empty strings. |
| `checksum` | Optional. Must match `sha256:<64-char lowercase hex>` format. |
| `signature` | Optional. Must match `hmac-sha256:<64-char lowercase hex>` format. |
| `prev_id` | Optional. Must be a valid 26-character ULID. |
| `tags` | Optional. Must be an object with non-empty string keys and values. |
