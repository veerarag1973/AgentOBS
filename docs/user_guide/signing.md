# HMAC Signing & Audit Chains

AgentOBS provides a cryptographic audit trail based on HMAC-SHA256. Every
signed event carries a payload checksum and a chain signature that links it to
its predecessor, forming a tamper-evident sequence that can detect deletions,
reorderings, and payload modifications.

## How signing works

```text
checksum  = sha256(canonical_payload_json)
sig_input = event_id + "|" + checksum + "|" + (prev_id or "")
signature = HMAC-SHA256(sig_input, org_secret)
```

The canonical payload JSON is compact (no whitespace) with sorted keys for
determinism. The resulting `checksum` and `signature` values are stored
directly on the event.

## Signing a single event

```python
from tracium import Event, EventType
from tracium.signing import sign, verify, assert_verified

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="my-tool@1.0.0",
    payload={"span_name": "chat"},
)

signed = sign(event, org_secret="my-org-secret")

assert signed.checksum is not None      # "sha256:..."
assert signed.signature is not None     # "hmac-sha256:..."
assert signed.prev_id is None           # first in chain

# Verify
assert verify(signed, org_secret="my-org-secret") is True

# Strict variant — raises VerificationError on failure
assert_verified(signed, org_secret="my-org-secret")
```

## Building an audit chain

Use `AuditStream` to build a chain where each event is linked to the previous
one via `prev_id`:

```python
from tracium import Event, EventType
from tracium.signing import AuditStream

stream = AuditStream(org_secret="my-org-secret", source="my-tool@1.0.0")

events_to_sign = [
    Event(event_type=EventType.TRACE_SPAN_COMPLETED,
          source="my-tool@1.0.0",
          payload={"index": i})
    for i in range(10)
]

for evt in events_to_sign:
    signed = stream.append(evt)     # returns signed event with prev_id set

print(len(stream))                  # 10
print(stream.events[0].prev_id)     # None — first event
print(stream.events[1].prev_id)     # == stream.events[0].event_id
```

## Verifying a chain

```python
from tracium.signing import verify_chain

result = stream.verify()             # or: verify_chain(events, org_secret="...")

assert result.valid                  # True if no tampering or gaps
assert result.tampered_count == 0    # number of events with bad signatures
assert result.gaps == []             # event_ids where prev_id linkage broke
assert result.first_tampered is None # first tampered event_id, or None
```

### Detecting tampering

```python
from tracium.signing import verify_chain

# Tamper with an event's payload after signing
signed_events = list(stream.events)
object.__setattr__(signed_events[3], "_payload", {"hacked": True})

result = verify_chain(signed_events, org_secret="my-org-secret")
assert not result.valid
assert result.tampered_count >= 1
assert result.first_tampered == signed_events[3].event_id
```

### Detecting deletions (gaps)

```python
# Remove event index 2 from the chain
events_with_gap = [e for i, e in enumerate(stream.events) if i != 2]

result = verify_chain(events_with_gap, org_secret="my-org-secret")
assert not result.valid
assert stream.events[3].event_id in result.gaps
```

## Key rotation

For long-lived audit streams, rotate the signing key periodically. The
rotation event itself is signed with the **old** key, providing continuity:

```python
stream = AuditStream(org_secret="old-secret", source="my-tool@1.0.0")

# ... append events ...

rotation_event = stream.rotate_key(
    "new-secret-v2",
    metadata={"reason": "scheduled", "rotated_by": "ops-team"},
)

# Subsequent events are signed with "new-secret-v2"
# Verification still works across the rotation boundary:
result = stream.verify()
assert result.valid
```

## Higher-level compliance wrapper

The `tracium.compliance` module provides a richer wrapper over
`verify_chain()` that includes gap reporting, violation objects, and
timestamp monotonicity checks:

```python
from tracium.compliance import verify_chain_integrity

result = verify_chain_integrity(events, org_secret="my-org-secret")
if not result:
    for v in result.violations:
        print(f"[{v.violation_type}] {v.event_id}: {v.detail}")
```

See [compliance.md](compliance.md) for the full compliance API.
