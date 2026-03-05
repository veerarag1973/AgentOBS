# tracium.validate

JSON Schema validation for `Event` envelopes.

Validates `Event` instances against the published JSON Schema in
`schemas/v1.0/schema.json`. When the optional `jsonschema` package is installed,
full Draft 2020-12 validation is performed. Otherwise a stdlib-only structural
check is run that covers all required fields, types, and regex patterns.

**Install for full validation:**

```bash
pip install "tracium[jsonschema]"
```

---

## Module-level functions

### `validate_event(event: Event) -> None`

Validate `event` against the published v1.0 JSON Schema.

Serialises `event` to a plain dict and validates the envelope structure.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | The `Event` instance to validate. |

**Raises:**

- `SchemaValidationError` — if the event does not conform to the envelope schema.
- `FileNotFoundError` — if `schemas/v1.0/schema.json` is missing from the distribution.
- `TypeError` — if `event` is not an `Event` instance.

**Example:**

```python
from tracium import Event, EventType
from tracium.validate import validate_event

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="llm-trace@0.3.1",
    payload={"span_name": "run", "status": "ok"},
)
validate_event(event)  # passes silently
```

---

### `load_schema() -> Dict[str, Any]`

Load and cache the v1.0 JSON Schema from disk.

The schema is loaded once and cached in memory for subsequent calls.

**Returns:** `Dict[str, Any]` — parsed JSON Schema as a plain Python dict.

**Raises:** `FileNotFoundError` — if `schemas/v1.0/schema.json` cannot be found relative to the package root.

---

## Validation rules

| Field | Rule |
|-------|------|
| `schema_version` | Required. Must match SemVer pattern (e.g. `"1.0"`). |
| `event_id` | Required. Must be a valid 26-character ULID. |
| `event_type` | Required. Must match `^(?:llm\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}|[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*){2,}\.[a-z][a-z0-9_]*)$`. |
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
