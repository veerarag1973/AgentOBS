# tracium.signing

HMAC-SHA256 event signing, chain verification, and the `AuditStream` class.

See the [Signing User Guide](../user_guide/signing.md) for full usage examples.

---

## `ChainVerificationResult`

```python
@dataclass(frozen=True)
class ChainVerificationResult:
    valid: bool
    first_tampered: Optional[str]
    gaps: List[str]
    tampered_count: int
```

Result of a `verify_chain()` call.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `valid` | `bool` | `True` when the entire chain verified without gaps or tampered events. |
| `first_tampered` | `str \| None` | `event_id` of the first tampered event, or `None` if all verified. |
| `gaps` | `List[str]` | List of `event_id` strings where the chain has broken `prev_id` links. |
| `tampered_count` | `int` | Total number of events that failed HMAC verification. |

---

## Module-level functions

### `sign(event, org_secret, prev_event=None) -> Event`

```python
def sign(
    event: Event,
    org_secret: str,
    prev_event: Optional[Event] = None,
) -> Event
```

Sign an event with HMAC-SHA256 and return a **new** event with `checksum`,
`signature`, and (if `prev_event` is provided) `prev_id` set.

The original event is **not** mutated.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | The event to sign. Must have a valid `event_id`. |
| `org_secret` | `str` | HMAC secret for the organisation. Never included in logs or exceptions. |
| `prev_event` | `Event \| None` | Preceding event in the audit chain. Sets `prev_id` on the returned event. |

**Returns:** `Event` — a new event with `checksum`, `signature`, and optionally `prev_id` populated.

**Raises:** `SigningError` — if `org_secret` is empty or the event has no `event_id`.

**Example:**

```python
from tracium.signing import sign

signed = sign(event, org_secret="my-secret")
assert signed.signature is not None
```

---

### `verify(event, org_secret) -> bool`

```python
def verify(event: Event, org_secret: str) -> bool
```

Return `True` if the event's HMAC signature is valid.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | A previously signed event. |
| `org_secret` | `str` | The secret used when signing. |

**Returns:** `bool` — `True` if the signature is valid, `False` otherwise.

**Raises:** `SigningError` — if `org_secret` is empty or whitespace-only.

---

### `assert_verified(event, org_secret) -> None`

```python
def assert_verified(event: Event, org_secret: str) -> None
```

Raise an exception if the event's signature is invalid.

Equivalent to: `if not verify(event, secret): raise VerificationError(event.event_id)`

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | A previously signed event. |
| `org_secret` | `str` | The secret used when signing. |

**Raises:** `VerificationError` — if the signature does not match. `SigningError` — if `org_secret` is empty or whitespace-only.

---

### `verify_chain(events, org_secret, key_map=None) -> ChainVerificationResult`

```python
def verify_chain(
    events: Sequence[Event],
    org_secret: str,
    key_map: Optional[Dict[str, str]] = None,
) -> ChainVerificationResult
```

Verify an entire ordered sequence of signed events as a tamper-evident chain.

Checks each event's HMAC signature and validates that `prev_id` links are
continuous. Returns a `ChainVerificationResult` summarising any gaps or
tampered events.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `events` | `Sequence[Event]` | Ordered list of events from oldest to newest. |
| `org_secret` | `str` | Default HMAC secret for all events. |
| `key_map` | `Dict[str, str] \| None` | Optional mapping of `event_id → secret` for per-event key rotation support. |

**Returns:** `ChainVerificationResult`

**Raises:** `SigningError` — if `org_secret` is empty.

---

## `AuditStream`

```python
class AuditStream(org_secret: str, source: str)
```

A stateful, append-only audit stream that automatically signs each event and
maintains a tamper-evident chain.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `org_secret` | `str` | HMAC signing secret. Must be non-empty. |
| `source` | `str` | Source string for auto-generated audit events (e.g. `"my-service@1.0.0"`). |

**Raises:** `SigningError` — if `org_secret` is empty.

**Example:**

```python
from tracium.signing import AuditStream

stream = AuditStream(org_secret="my-secret", source="my-service@1.0.0")
signed = stream.append(event)
result = stream.verify()
print(result.valid)  # True
```

### Properties

#### `events -> List[Event]`

A read-only copy of all signed events in the stream.

### Methods

#### `append(event: Event) -> Event`

Sign and append an event to the stream.

The event is signed with the current `org_secret`, with `prev_id` pointing to
the last event in the chain. Returns the signed event.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | The event to append. |

**Returns:** `Event` — the signed event with `checksum`, `signature`, and `prev_id` set.

**Raises:** `SigningError` — if signing fails.

---

#### `rotate_key(new_secret: str, metadata: Optional[Dict[str, Any]] = None) -> Event`

Rotate the HMAC signing key.

Appends a signed `llm.audit.key.rotated` sentinel event (signed with the **old**
key), then switches to `new_secret` for subsequent events.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `new_secret` | `str` | The new HMAC secret to use going forward. Must be non-empty. |
| `metadata` | `Dict[str, Any] \| None` | Optional metadata to include in the key-rotation event's payload. |

**Returns:** `Event` — the signed key-rotation sentinel event.

**Raises:** `SigningError` — if `new_secret` is empty.

---

#### `verify() -> ChainVerificationResult`

Verify the entire chain held by this stream.

Uses `verify_chain()` internally with the current `org_secret`.

**Returns:** `ChainVerificationResult`
