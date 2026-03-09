"""HMAC-SHA256 signing and tamper-evident audit chain for agentobs.

Provides compliance-grade audit log integrity without requiring a blockchain
or external service.  All cryptography uses pure Python stdlib — no network
calls, no external dependencies.

Signing algorithm
-----------------

Each event is signed in two steps::

    checksum  = "sha256:"      + sha256(canonical_payload_json).hexdigest()
    sig_input = event_id + "|" + checksum + "|" + (prev_id or "")
    signature = "hmac-sha256:" + HMAC-SHA256(sig_input, org_secret).hexdigest()

The *canonical payload JSON* uses ``sort_keys=True, separators=(",", ":")``
(compact, no whitespace) so the same payload always produces the same checksum
regardless of dict insertion order or Python version.

Chain linkage
-------------

Each event (except the first) stores the ``prev_id`` of its predecessor.
A missing or mismatched ``prev_id`` indicates a deleted or reordered event::

    events[n].prev_id == events[n-1].event_id   # must hold for every n > 0

Key rotation
------------

The HMAC key can be rotated mid-chain using :meth:`AuditStream.rotate_key`.
A key-rotation event (``EventType.AUDIT_KEY_ROTATED``) is inserted into the
chain, signed with the *current* key.  All subsequent events are signed with
the *new* key.  :func:`verify_chain` accepts a ``key_map`` argument that maps
rotation event IDs to the corresponding new secrets, enabling independent
chain verification across rotation boundaries.

Security requirements
---------------------

*   The ``org_secret`` **never** appears in exception messages, ``__repr__``,
    ``__str__``, or ``__reduce__`` output.
*   Signing failures always raise :exc:`~agentobs.exceptions.SigningError`
    — never silently pass.
*   Empty or whitespace-only secrets are rejected immediately.
*   :func:`verify` uses :func:`hmac.compare_digest` for all comparisons to
    prevent timing-based side-channel attacks.

Usage
-----
::

    from agentobs import Event, EventType
    from agentobs.signing import sign, verify, verify_chain, AuditStream

    # Sign a single event
    signed = sign(event, org_secret="corp-key-001")
    assert verify(signed, org_secret="corp-key-001")

    # Build a verifiable chain
    stream = AuditStream(org_secret="corp-key-001", source="audit-daemon@1.0.0")
    for evt in raw_events:
        stream.append(evt)

    result = stream.verify()
    # result.valid          → True
    # result.first_tampered → None
    # result.gaps           → []
    # result.tampered_count → 0
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentobs.exceptions import SigningError, VerificationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentobs.event import Event

__all__ = [
    "AuditStream",
    "ChainVerificationResult",
    "assert_verified",
    "sign",
    "verify",
    "verify_chain",
]


# ---------------------------------------------------------------------------
# ChainVerificationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainVerificationResult:
    """Immutable result returned by :func:`verify_chain` and :meth:`AuditStream.verify`.

    Attributes:
        valid:          ``True`` only if **all** signatures are valid, **no**
                        ``prev_id`` linkage gaps exist, and no tampering was
                        found.
        first_tampered: The ``event_id`` of the first event whose signature
                        did not verify, or ``None`` if the chain is clean.
        gaps:           List of ``event_id`` values where the expected
                        ``prev_id`` linkage is broken — each entry represents
                        a potential deletion or reordering.
        tampered_count: Total number of events with invalid signatures across
                        the entire chain.
    """

    valid: bool
    first_tampered: str | None
    gaps: list[str]
    tampered_count: int


# ---------------------------------------------------------------------------
# Internal crypto helpers
# ---------------------------------------------------------------------------


def _canonical_payload_bytes(payload: dict) -> bytes:
    """Return compact, sorted UTF-8 JSON bytes for *payload*.

    Uses ``sort_keys=True`` for determinism across Python versions and
    ``separators=(",", ":")`` to eliminate optional whitespace.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _compute_checksum(payload: dict) -> str:
    """Return ``"sha256:<hex>"`` digest of the canonical payload JSON."""
    digest = hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()
    return f"sha256:{digest}"


def _compute_signature(
    event_id: str,
    checksum: str,
    prev_id: str | None,
    org_secret: str,
) -> str:
    """Return ``"hmac-sha256:<hex>"`` signature.

    Message is ``"{event_id}|{checksum}|{prev_id or ''}"`` encoded as UTF-8.
    """
    msg = f"{event_id}|{checksum}|{prev_id or ''}"
    mac = _hmac.new(
        key=org_secret.encode("utf-8"),
        msg=msg.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return f"hmac-sha256:{mac.hexdigest()}"


def _validate_secret(org_secret: str) -> None:
    """Raise :exc:`~agentobs.exceptions.SigningError` if *org_secret* is empty or whitespace-only.

    Security: the value of *org_secret* is **never** included in the error
    message.
    """
    if not isinstance(org_secret, str) or not org_secret.strip():
        raise SigningError("org_secret must be a non-empty, non-whitespace string")


# ---------------------------------------------------------------------------
# Public signing API
# ---------------------------------------------------------------------------


def sign(
    event: Event,
    org_secret: str,
    prev_event: Event | None = None,
) -> Event:
    """Sign *event* and return a new event with ``checksum``, ``signature``, and ``prev_id`` set.

    The original *event* is not mutated — a new
    :class:`~agentobs.event.Event` instance is returned.

    Signing steps::

        checksum  = sha256(canonical_payload_json)
        sig_input = event_id + "|" + checksum + "|" + (prev_id or "")
        signature = HMAC-SHA256(sig_input, org_secret)

    Args:
        event:       The event to sign.
        org_secret:  HMAC signing key (non-empty string).
        prev_event:  The immediately preceding event in the audit chain, or
                     ``None`` if *event* is the first in the chain.

    Returns:
        A new :class:`~agentobs.event.Event` with ``checksum``, ``signature``,
        and (if *prev_event* is given) ``prev_id`` populated.

    Raises:
        SigningError: If *org_secret* is empty or whitespace-only.

    Example::

        signed = sign(event, org_secret="my-key")
        assert signed.checksum.startswith("sha256:")
        assert signed.signature.startswith("hmac-sha256:")
    """
    # Deferred import to avoid circular dependency at module-load time.
    from agentobs.event import Event  # noqa: PLC0415

    _validate_secret(org_secret)

    prev_id: str | None = prev_event.event_id if prev_event is not None else None
    checksum = _compute_checksum(dict(event.payload))
    signature = _compute_signature(event.event_id, checksum, prev_id, org_secret)

    return Event(
        schema_version=event.schema_version,
        event_id=event.event_id,
        event_type=event.event_type,
        timestamp=event.timestamp,
        source=event.source,
        payload=dict(event.payload),
        trace_id=event.trace_id,
        span_id=event.span_id,
        parent_span_id=event.parent_span_id,
        org_id=event.org_id,
        team_id=event.team_id,
        actor_id=event.actor_id,
        session_id=event.session_id,
        tags=event.tags,
        checksum=checksum,
        signature=signature,
        prev_id=prev_id,
    )


def verify(event: Event, org_secret: str) -> bool:
    """Verify the checksum and HMAC signature of a single signed event.

    Uses :func:`hmac.compare_digest` for both comparisons to guard against
    timing-based side-channel attacks.

    Args:
        event:      The event to verify.
        org_secret: HMAC signing key used when the event was signed.

    Returns:
        ``True`` if both the checksum and signature are cryptographically
        valid.  ``False`` if either fails (tampered payload, wrong key, or
        missing checksum/signature).

    Raises:
        SigningError: If *org_secret* is empty or whitespace-only.

    Note:
        This function deliberately returns ``False`` rather than raising for
        tampered events — :func:`verify_chain` calls it in a loop and needs
        to accumulate all failures.  For the strict raising variant see
        :func:`assert_verified`.

    Example::

        if not verify(event, org_secret="my-key"):
            raise RuntimeError(f"Tampered event: {event.event_id}")
    """
    _validate_secret(org_secret)

    if event.checksum is None or event.signature is None:
        return False

    expected_checksum = _compute_checksum(dict(event.payload))
    if not _hmac.compare_digest(event.checksum, expected_checksum):
        return False

    expected_signature = _compute_signature(
        event.event_id, event.checksum, event.prev_id, org_secret
    )
    return _hmac.compare_digest(event.signature, expected_signature)


def assert_verified(event: Event, org_secret: str) -> None:
    """Assert that *event* passes cryptographic verification.

    Strict variant of :func:`verify` that raises instead of returning
    ``False``.

    Args:
        event:      The event to verify.
        org_secret: HMAC signing key used when the event was signed.

    Raises:
        VerificationError: If :func:`verify` returns ``False``.
        SigningError:      If *org_secret* is empty or whitespace-only.

    Example::

        assert_verified(event, org_secret="my-key")   # raises on tamper
    """
    if not verify(event, org_secret):
        raise VerificationError(event_id=event.event_id)


def _check_event_signature(
    event: "Event",
    current_secret: str,
    first_tampered: str | None,
    tampered_count: int,
) -> tuple[str | None, int]:
    """Check signature validity; returns updated (first_tampered, tampered_count)."""
    if not verify(event, current_secret):
        tampered_count += 1
        if first_tampered is None:
            first_tampered = event.event_id
    return first_tampered, tampered_count


def _check_chain_linkage(
    event: "Event",
    i: int,
    event_list: list["Event"],
    gaps: list[str],
) -> None:
    """Check chain linkage; appends to gaps if a gap is detected."""
    if i == 0:
        if event.prev_id is not None:
            gaps.append(event.event_id)
    else:
        expected_prev = event_list[i - 1].event_id
        if event.prev_id != expected_prev:
            gaps.append(event.event_id)


def verify_chain(
    events: Sequence[Event],
    org_secret: str,
    key_map: dict[str, str] | None = None,
) -> ChainVerificationResult:
    """Verify an entire ordered sequence of signed events as an audit chain.

    Performs three checks per event:

    1. **Signature validity** — recomputes checksum and HMAC; flags mismatches.
    2. **Chain linkage** — ``events[n].prev_id == events[n-1].event_id``.
    3. **Head integrity** — ``events[0].prev_id`` must be ``None`` (no missing
       predecessor); non-``None`` signals an undetected gap at the head.

    Key rotation
    ~~~~~~~~~~~~
    Pass ``key_map`` to handle chains that span a key rotation.  The dict maps
    a rotation event's ``event_id`` to the new secret that takes effect
    **after** that event is verified::

        result = verify_chain(events, org_secret="old-key",
                              key_map={"<rotation_event_id>": "new-key"})

    Args:
        events:      Ordered sequence of events (earliest first).  May be
                     empty — returns ``valid=True`` with no failures.
        org_secret:  HMAC signing key for the first chain segment.
        key_map:     Optional ``{rotation_event_id: new_secret}`` dict
                     enabling multi-segment verification after key rotation.

    Returns:
        A :class:`ChainVerificationResult` with ``valid``, ``first_tampered``,
        ``gaps``, and ``tampered_count``.

    Raises:
        SigningError: If *org_secret* (or any value in *key_map*) is empty.

    Example::

        result = verify_chain(signed_events, org_secret="my-key")
        if not result.valid:
            print(f"First tampered: {result.first_tampered}")
            print(f"Gaps (deleted events): {result.gaps}")
    """
    _validate_secret(org_secret)
    if key_map:
        for new_secret in key_map.values():
            _validate_secret(new_secret)

    current_secret = org_secret
    km = key_map or {}

    first_tampered: str | None = None
    gaps: list[str] = []
    tampered_count = 0

    event_list = list(events)

    for i, event in enumerate(event_list):
        first_tampered, tampered_count = _check_event_signature(
            event, current_secret, first_tampered, tampered_count
        )
        _check_chain_linkage(event, i, event_list, gaps)
        if event.event_id in km:
            current_secret = km[event.event_id]

    valid = tampered_count == 0 and len(gaps) == 0
    return ChainVerificationResult(
        valid=valid,
        first_tampered=first_tampered,
        gaps=gaps,
        tampered_count=tampered_count,
    )


# ---------------------------------------------------------------------------
# AuditStream
# ---------------------------------------------------------------------------


class AuditStream:
    """Tamper-evident HMAC-signed audit chain stream.

    Sequential event stream that HMAC-signs every appended event and links
    them via ``prev_id``, forming a tamper-evident audit chain.

    The signing secret is **never** exposed in :func:`repr`, :func:`str`, or
    any exception message.

    Args:
        org_secret: HMAC signing key (non-empty string).
        source:     The ``source`` field used for auto-generated audit events
                    such as key-rotation events.  Must follow the
                    ``tool-name@x.y.z`` format accepted by
                    :class:`~agentobs.event.Event`.

    Raises:
        SigningError: If *org_secret* is empty or whitespace-only.

    Example::

        stream = AuditStream(org_secret="corp-key", source="audit-daemon@1.0.0")
        for event in events:
            stream.append(event)
        stream.rotate_key("corp-key-v2", metadata={"reason": "scheduled"})
        result = stream.verify()
        assert result.valid
    """

    __slots__ = ("_events", "_initial_secret", "_key_map", "_org_secret", "_source")

    def __init__(self, org_secret: str, source: str) -> None:
        _validate_secret(org_secret)
        object.__setattr__(self, "_initial_secret", org_secret)
        object.__setattr__(self, "_org_secret", org_secret)
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_events", [])
        # maps rotation_event_id → new_secret for verify()
        object.__setattr__(self, "_key_map", {})

    def __setattr__(self, name: str, value: object) -> None:  # type: ignore[override]
        """Block external attribute mutation.

        Internal code uses :func:`object.__setattr__` directly.
        """
        raise AttributeError(
            f"AuditStream is immutable externally — attribute '{name}' cannot be set. "
            "Use append() or rotate_key() to modify the stream."
        )

    def __repr__(self) -> str:
        """Safe repr that never exposes the signing secret."""
        return f"<AuditStream events={len(self._events)}>"  # type: ignore[arg-type]

    def __str__(self) -> str:
        return f"<AuditStream events={len(self._events)}>"  # type: ignore[arg-type]

    def __len__(self) -> int:
        return len(self._events)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def events(self) -> list[Event]:
        """A read-only copy of all signed events in the stream.

        Returns a new list each call so callers cannot mutate the internal
        state.
        """
        return list(self._events)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Mutation methods (guarded)
    # ------------------------------------------------------------------

    def append(self, event: Event) -> Event:
        """Sign *event*, link it to the chain, append, and return the signed event.

        The given *event* is not mutated.  A new
        :class:`~agentobs.event.Event` with ``checksum``, ``signature``, and
        ``prev_id`` set is returned **and** stored in the stream.

        Args:
            event: The unsigned (or partially signed) event to add.

        Returns:
            The freshly signed event with full chain linkage.

        Raises:
            SigningError: If the current signing key is somehow invalid
                          (should not happen if the stream was constructed
                          correctly).
        """
        events_list: list[Event] = self._events  # type: ignore[assignment]
        prev_event: Event | None = events_list[-1] if events_list else None
        signed = sign(event, self._org_secret, prev_event=prev_event)  # type: ignore[arg-type]
        events_list.append(signed)
        return signed

    def rotate_key(
        self,
        new_secret: str,
        metadata: dict[str, str] | None = None,
    ) -> Event:
        """Rotate the signing key: append a key-rotation event and switch keys.

        The key-rotation event is signed with the **current** key, ensuring
        continuity of the chain at the rotation boundary.  All events appended
        after this call are signed with *new_secret*.

        Args:
            new_secret: The new HMAC signing key (non-empty string).
            metadata:   Optional ``str → str`` payload fields for the
                        rotation event (e.g. ``{"reason": "scheduled",
                        "rotated_by": "ops-team"}``).

        Returns:
            The signed key-rotation :class:`~agentobs.event.Event`.

        Raises:
            SigningError: If *new_secret* is empty or whitespace-only.

        Example::

            stream.rotate_key("new-secret-v2", metadata={"reason": "annual"})
        """
        # Deferred imports to avoid circular dependency at module-load time.
        from agentobs.event import Event  # noqa: PLC0415
        from agentobs.types import EventType  # noqa: PLC0415

        _validate_secret(new_secret)

        payload: dict[str, str] = {"rotation_marker": "true"}
        if metadata:
            payload.update(metadata)

        rotation_event = Event(
            event_type=EventType.AUDIT_KEY_ROTATED,
            source=self._source,  # type: ignore[arg-type]
            payload=payload,
        )

        # Sign with the CURRENT key (before rotation)
        signed_rotation = self.append(rotation_event)

        # After this event_id, use new_secret for subsequent events
        key_map: dict[str, str] = self._key_map  # type: ignore[assignment]
        key_map[signed_rotation.event_id] = new_secret

        # Switch active key for future appends
        object.__setattr__(self, "_org_secret", new_secret)

        return signed_rotation

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self) -> ChainVerificationResult:
        """Verify the entire chain, respecting any key-rotation boundaries.

        Internally calls :func:`verify_chain` with the initial secret and the
        accumulated ``key_map`` from all :meth:`rotate_key` calls.

        Returns:
            A :class:`ChainVerificationResult` reflecting the state of the
            complete chain.
        """
        key_map: dict[str, str] = self._key_map  # type: ignore[assignment]
        return verify_chain(
            self._events,  # type: ignore[arg-type]
            org_secret=self._initial_secret,  # type: ignore[arg-type]
            key_map=dict(key_map) if key_map else None,
        )
