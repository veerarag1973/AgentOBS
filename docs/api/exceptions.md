# tracium.exceptions

Typed exception hierarchy for Tracium.

All exceptions inherit from `LLMSchemaError`, allowing callers to catch the
entire family with a single `except LLMSchemaError`.

**Security:** HMAC keys and PII-tagged content are **never** embedded in exception
messages or `__cause__` chains.

---

## Exception hierarchy

```
LLMSchemaError
├── SchemaValidationError
├── ULIDError
├── SerializationError
├── DeserializationError
├── EventTypeError
├── SigningError
├── VerificationError
└── ExportError
```

---

## `LLMSchemaError`

```python
class LLMSchemaError(Exception)
```

Base class for all tracium exceptions.

Write a single broad guard:

```python
try:
    ...
except LLMSchemaError as exc:
    logger.error("tracium error: %s", exc)
```

---

## `SchemaValidationError`

```python
class SchemaValidationError(LLMSchemaError)
SchemaValidationError(field: str, received: object, reason: str)
```

Raised when an `Event` fails field-level validation.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `field` | `str` | The dotted field path that failed (e.g. `"event_id"`). |
| `received` | `Any` | The actual value that was provided (redacted if sensitive). |
| `reason` | `str` | Human-readable explanation of the constraint violated. |

**Example:**

```python
try:
    event.validate()
except SchemaValidationError as exc:
    logger.error("field=%s reason=%s", exc.field, exc.reason)
```

---

## `ULIDError`

```python
class ULIDError(LLMSchemaError)
ULIDError(detail: str)
```

Raised when ULID generation or parsing fails.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `detail` | `str` | Human-readable description of the failure. |

---

## `SerializationError`

```python
class SerializationError(LLMSchemaError)
SerializationError(event_id: str, reason: str)
```

Raised when an `Event` cannot be serialised to JSON.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `event_id` | `str` | The ULID of the event that failed (safe to log). |
| `reason` | `str` | Human-readable description of the failure. |

---

## `DeserializationError`

```python
class DeserializationError(LLMSchemaError)
DeserializationError(reason: str, source_hint: str = "<unknown>")
```

Raised when a JSON blob cannot be deserialised into an `Event`.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `reason` | `str` | Human-readable description of the failure. |
| `source_hint` | `str` | A short, non-PII hint about the source (e.g. filename). |

---

## `EventTypeError`

```python
class EventTypeError(LLMSchemaError)
EventTypeError(event_type: str, reason: str)
```

Raised when an unknown or malformed event type string is encountered.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | The offending event type string. |
| `reason` | `str` | Human-readable description of the failure. |

---

## `SigningError`

```python
class SigningError(LLMSchemaError)
SigningError(reason: str)
```

Raised when HMAC event signing fails.

The `org_secret` value is **never** included in the message.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `reason` | `str` | Human-readable description of why signing failed. |

---

## `VerificationError`

```python
class VerificationError(LLMSchemaError)
VerificationError(event_id: str)
```

Raised by `assert_verified()` when an event fails cryptographic verification.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `event_id` | `str` | The ULID of the event that failed (safe to log). |

---

## `ExportError`

```python
class ExportError(LLMSchemaError)
ExportError(backend: str, reason: str, event_id: str = "")
```

Raised when exporting events to an external backend fails.

HMAC secrets and PII-tagged payloads are **never** embedded in the message.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `backend` | `str` | Short identifier for the backend (e.g. `"otlp"`, `"webhook"`, `"jsonl"`). |
| `reason` | `str` | Human-readable description of the failure. |
| `event_id` | `str` | The ULID of the failed event, or `""` for batch failures. |

**Example:**

```python
try:
    await exporter.export(event)
except ExportError as exc:
    logger.error("backend=%s reason=%s", exc.backend, exc.reason)
```

---

## `SchemaVersionError`

```python
class SchemaVersionError(LLMSchemaError)
SchemaVersionError(version: str)
```

Raised when an event carries an unsupported `schema_version` value.

v2.0 consumers MUST accept `"1.0"` and `"2.0"` and MUST raise this error for
any other value (RFC-0001 §15.5).

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `version` | `str` | The unsupported schema version string that was encountered. |

**Example:**

```python
try:
    consumer.ingest(event)
except SchemaVersionError as exc:
    logger.warning("Skipping event with unknown version %s", exc.version)
```

---

## `SigningError`

```python
class SigningError(LLMSchemaError)
SigningError(reason: str)
```

Raised when HMAC event signing fails.

The `org_secret` value is **never** included in the message.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `reason` | `str` | Human-readable description of why signing failed. |

---

## `VerificationError`

```python
class VerificationError(LLMSchemaError)
VerificationError(event_id: str)
```

Raised by `assert_verified()` when an event fails cryptographic verification.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `event_id` | `str` | The ULID of the event that failed (safe to log). |

---

## `ExportError`

```python
class ExportError(LLMSchemaError)
ExportError(backend: str, reason: str, event_id: str = "")
```

Raised when exporting events to an external backend fails.

HMAC secrets and PII-tagged payloads are **never** embedded in the message.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `backend` | `str` | Short identifier for the backend (e.g. `"otlp"`, `"webhook"`, `"jsonl"`). |
| `reason` | `str` | Human-readable description of the failure. |
| `event_id` | `str` | The ULID of the failed event, or `""` for batch failures. |

**Example:**

```python
try:
    await exporter.export(event)
except ExportError as exc:
    logger.error("backend=%s reason=%s", exc.backend, exc.reason)
```
