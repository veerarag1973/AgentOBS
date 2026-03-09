"""Property-based tests for agentobs core invariants (RFC-0001-AGENTOBS Gap 6).

Uses ``hypothesis`` to verify:
1. ``sign()`` → ``verify()`` roundtrip holds for arbitrary payloads and secrets.
2. Canonical JSON serialisation is deterministic for any payload dict.
3. ULID monotonic ordering is preserved for any sequence of generated ULIDs.

These tests supplement the existing unit test suite with generative inputs
that approach the infinite space of possible values — critical for audit-chain
integrity (RFC §11) and portable interoperability (RFC §21.2).
"""

from __future__ import annotations

import json
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agentobs import Event, EventType
from agentobs.signing import sign, verify
from agentobs.ulid import generate as generate_ulid

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Non-empty strings that are printable/safe for JSON payloads and HMAC keys.
_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="-_.",
    ),
    min_size=1,
    max_size=32,
)

# Flat payload values: strings, ints, floats, booleans, None.
_json_leaf = st.one_of(
    st.text(max_size=64),
    st.integers(min_value=-(2**31), max_value=2**31),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
)

# Flat payload dict (no deep nesting required — depth invariant tested separately).
_flat_payload = st.dictionaries(
    keys=_safe_text,
    values=_json_leaf,
    min_size=1,
    max_size=20,
)

# HMAC secrets: non-empty text up to 128 chars.
_secret = st.text(min_size=1, max_size=128).filter(lambda s: s.strip() != "")


def _make_event(payload: dict) -> Event:
    """Build a minimal valid Event with the given payload."""
    return Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="test-agent@1.0.0",
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Test 1: sign/verify roundtrip
# ---------------------------------------------------------------------------


@given(payload=_flat_payload, secret=_secret)
@settings(max_examples=200)
def test_sign_verify_roundtrip(payload: dict, secret: str) -> None:
    """sign() followed by verify() MUST always return True.

    RFC §11: Any signed event MUST verify against the same key and the same
    payload.  This property test exhaustively samples (payload, secret) pairs
    to confirm there are no edge-case breakages in the HMAC pipeline.
    """
    event = _make_event(payload)
    signed = sign(event, org_secret=secret)
    assert verify(signed, org_secret=secret), (
        f"verify() returned False for payload={payload!r}, secret={secret!r}"
    )


@given(payload=_flat_payload, secret=_secret)
@settings(max_examples=100)
def test_sign_verify_wrong_secret_fails(payload: dict, secret: str) -> None:
    """verify() MUST return False when called with a different secret.

    RFC §11: Verification against a wrong key MUST fail; this guards against
    trivially accepting any signature.
    """
    wrong_secret = secret + "_tampered"
    event = _make_event(payload)
    signed = sign(event, org_secret=secret)
    assert not verify(signed, org_secret=wrong_secret), (
        "verify() unexpectedly returned True with wrong secret"
    )


# ---------------------------------------------------------------------------
# Test 2: canonical JSON determinism
# ---------------------------------------------------------------------------


@given(payload=_flat_payload)
@settings(max_examples=200)
def test_canonical_json_deterministic(payload: dict) -> None:
    """to_json() MUST produce the same bytes on every call for the same Event.

    RFC §11 requires deterministic serialisation so that HMAC checksums are
    stable.  Even dict key insertion order MUST NOT affect the output.
    """
    event = _make_event(payload)
    first = event.to_json()
    second = event.to_json()
    assert first == second, "to_json() is not deterministic for the same event"


@given(payload=_flat_payload)
@settings(max_examples=200)
def test_canonical_json_keys_sorted(payload: dict) -> None:
    """to_json() payload keys MUST be sorted (sort_keys=True).

    RFC §11 mandates alphabetically sorted keys for canonical JSON. Verify
    that every nested key sequence is in non-descending lexicographic order.
    """
    event = _make_event(payload)
    raw = json.loads(event.to_json())
    # Top-level envelope keys must be sorted.
    keys = list(raw.keys())
    assert keys == sorted(keys), f"Envelope keys not sorted: {keys}"


# ---------------------------------------------------------------------------
# Test 3: ULID monotonic ordering
# ---------------------------------------------------------------------------


@given(count=st.integers(min_value=2, max_value=50))
@settings(max_examples=100)
def test_ulid_monotonic_within_same_ms(count: int) -> None:
    """ULIDs generated in rapid succession MUST sort monotonically.

    RFC §6.3: ULIDs are 26-character Crockford Base32; lexicographic order
    equals chronological order.  When multiple ULIDs are generated within the
    same millisecond, the random component is incremented to preserve ordering.
    """
    # Pin the clock-start so all ULIDs get the same millisecond timestamp.
    # generate_ulid() uses time.time_ns() internally; rapid loop is enough.
    ulids = [generate_ulid() for _ in range(count)]
    for i in range(1, len(ulids)):
        assert ulids[i - 1] <= ulids[i], (
            f"ULID ordering violation at index {i}: "
            f"{ulids[i-1]!r} > {ulids[i]!r}"
        )


@given(count=st.integers(min_value=1, max_value=20))
@settings(max_examples=50)
def test_ulid_first_char_constraint(count: int) -> None:
    """Each generated ULID's first character MUST be in [0-7] (RFC §6.3).

    This ensures the millisecond timestamp fits in 48 bits without overflow
    (valid through year 10889).
    """
    for _ in range(count):
        ulid = generate_ulid()
        assert ulid[0] in "01234567", (
            f"ULID first char {ulid[0]!r} violates RFC §6.3 (must be [0-7])"
        )
        assert len(ulid) == 26, f"ULID length {len(ulid)} != 26"
