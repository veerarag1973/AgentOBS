# llm.audit — Audit Chain Events

> **Auto-documented module:** `agentobs.namespaces.audit`

The RFC-canonical `llm.audit.*` namespace currently includes one registered
first-party event type:

- `llm.audit.key.rotated`

The `agentobs.namespaces.audit` module also ships helper payload dataclasses
for chain-verification outcomes. Those payloads are useful, but their
corresponding event types are **not** part of the canonical RFC Appendix B
registry. If you use them, emit them under a reverse-domain custom event type
(e.g. `x.mycompany.audit.chain.verified`) rather than `llm.*`.

## Payload classes

| Class | Canonical event type | Notes |
|-------|----------------------|-------|
| `AuditKeyRotatedPayload` | `llm.audit.key.rotated` | RFC Appendix B canonical event type |
| `AuditChainVerifiedPayload` | — | Use with a custom reverse-domain event type |
| `AuditChainTamperedPayload` | — | Use with a custom reverse-domain event type |

---

## `AuditKeyRotatedPayload`

Records that an HMAC signing key was replaced. `effective_from_event_id` is
the ULID of the first event signed with the new key, enabling exact replay
of any chain segment.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key_id` | `str` | ✓ | Identifier of the new key |
| `previous_key_id` | `str` | ✓ | Identifier of the superseded key |
| `rotated_at` | `str` | ✓ | ISO 8601 timestamp (6 decimal places) |
| `rotated_by` | `str` | ✓ | Identity of the operator or service that rotated the key |
| `rotation_reason` | `str \| None` | — | One of `"scheduled"`, `"suspected_compromise"`, `"policy_update"`, `"key_expiry"`, `"manual"` |
| `key_algorithm` | `str` | — | Defaults to `"HMAC-SHA256"` |
| `effective_from_event_id` | `str \| None` | — | ULID of first event signed with the new key |

### Canonical example

```python
from agentobs import Event, EventType
from agentobs.namespaces.audit import AuditKeyRotatedPayload

payload = AuditKeyRotatedPayload(
    key_id="key_01HX_v2",
    previous_key_id="key_01HX_v1",
    rotated_at="2026-03-04T12:00:00.000000Z",
    rotated_by="ops-bot@agentobs.io",
    rotation_reason="scheduled",
)

event = Event(
    event_type=EventType.AUDIT_KEY_ROTATED,
    source="key-manager@1.0.0",
    org_id="org_01HX",
    payload=payload.to_dict(),
)
```

---

## `AuditChainVerifiedPayload`

Represents a successful audit-chain verification result.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `verified_from_event_id` | `str` | ✓ | ULID of the first event in the verified range |
| `verified_to_event_id` | `str` | ✓ | ULID of the last event in the verified range |
| `event_count` | `int` | ✓ | Number of events verified |
| `verified_at` | `str` | ✓ | ISO 8601 timestamp of the verification run |
| `verified_by` | `str` | ✓ | Identity of the verifier (service or operator) |

### Custom event-type example

```python
from agentobs import Event
from agentobs.namespaces.audit import AuditChainVerifiedPayload

payload = AuditChainVerifiedPayload(
    verified_from_event_id="01HXABC0000000000000000000",
    verified_to_event_id="01HXABCZZZZZZZZZZZZZZZZZZZ",
    event_count=1024,
    verified_at="2026-03-04T14:00:00.000000Z",
    verified_by="audit-worker@1.0.0",
)

event = Event(
    event_type="x.mycompany.audit.chain.verified",
    source="audit-worker@1.0.0",
    org_id="org_01HX",
    payload=payload.to_dict(),
)
```

---

## `AuditChainTamperedPayload`

Represents a failed chain-verification result (tampering or sequence gaps).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `first_tampered_event_id` | `str` | ✓ | ULID of the first event with a broken HMAC |
| `tampered_count` | `int` | ✓ | Number of events with invalid signatures |
| `detected_at` | `str` | ✓ | ISO 8601 timestamp of detection |
| `detected_by` | `str` | ✓ | Identity of the detector |
| `gap_count` | `int \| None` | — | Number of missing sequence IDs |
| `gap_prev_ids` | `list[str]` | — | ULIDs immediately before each detected gap |
| `severity` | `str \| None` | — | `"low"`, `"medium"`, `"high"`, or `"critical"` |

### Custom event-type example

```python
from agentobs import Event
from agentobs.namespaces.audit import AuditChainTamperedPayload

payload = AuditChainTamperedPayload(
    first_tampered_event_id="01HXDEF0000000000000000000",
    tampered_count=3,
    detected_at="2026-03-04T15:30:00.000000Z",
    detected_by="audit-worker@1.0.0",
    severity="high",
    gap_count=1,
    gap_prev_ids=["01HXDEE0000000000000000000"],
)

event = Event(
    event_type="x.mycompany.audit.chain.tampered",
    source="audit-worker@1.0.0",
    org_id="org_01HX",
    payload=payload.to_dict(),
)
```
