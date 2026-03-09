"""agentobs.compliance._compat — RFC-0001 compatibility checks.

Verifies that a batch of :class:`~agentobs.event.Event` objects conform to the
schema requirements defined in RFC-0001 §15 (Conformance Profiles).

Public API
----------
- :class:`CompatibilityViolation` — single check failure record.
- :class:`CompatibilityResult`    — aggregated result for a batch.
- :func:`test_compatibility`      — run all checks against a list of events.
- :func:`_check_event`            — run all checks against a single event
                                    (internal, exposed for direct testing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "CompatibilityResult",
    "CompatibilityViolation",
    "test_compatibility",
]

# ---------------------------------------------------------------------------
# Check IDs (stable identifiers for downstream consumers)
# ---------------------------------------------------------------------------
#   CHK-1  Required envelope fields present and non-empty.
#   CHK-2  Event type is registered *or* passes custom-type validation.
#   CHK-3  Source conforms to the ``name@semver`` pattern.
#   CHK-5  Event ID is a valid 26-character ULID.

_REQUIRED_FIELDS_RULE = "Required envelope fields present"

@dataclass
class CompatibilityViolation:
    """A single failed compatibility check on one event.

    Attributes:
        check_id: Short stable identifier (e.g. ``"CHK-1"``).
        rule:     Human-readable rule description.
        detail:   Specific failure description for this event.
        event_id: The ``event_id`` of the offending event, or ``None``.
    """

    check_id: str
    rule: str
    detail: str
    event_id: str | None = None


@dataclass
class CompatibilityResult:
    """Aggregated result from :func:`test_compatibility`.

    Attributes:
        passed:         ``True`` only when *violations* is empty.
        events_checked: Number of events that were inspected.
        violations:     All failures found across the batch.
    """

    passed: bool
    events_checked: int
    violations: list[CompatibilityViolation] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


# ---------------------------------------------------------------------------
# Internal check runner
# ---------------------------------------------------------------------------


def _check_event(event: object) -> list[CompatibilityViolation]:  # type: ignore[type-arg]
    """Return a list of :class:`CompatibilityViolation` objects for *event*.

    Performs checks CHK-1, CHK-2, CHK-3, and CHK-5.  An empty list means the
    event is fully RFC-0001 compliant.
    """
    from agentobs.event import _SOURCE_PATTERN  # noqa: PLC0415
    from agentobs.types import (  # noqa: PLC0415
        EventTypeError,
        is_registered,
        validate_custom,
    )
    from agentobs.ulid import validate as _validate_ulid  # noqa: PLC0415

    violations: list[CompatibilityViolation] = []
    eid: str | None = getattr(event, "event_id", None)

    # ------------------------------------------------------------------
    # CHK-1: Required fields present and non-empty
    # ------------------------------------------------------------------
    schema_version = getattr(event, "schema_version", None)
    source = getattr(event, "source", None)
    payload = getattr(event, "payload", None)

    if not schema_version:
        violations.append(
            CompatibilityViolation(
                check_id="CHK-1",
                rule=_REQUIRED_FIELDS_RULE,
                detail="schema_version is empty or missing",
                event_id=eid,
            )
        )
    if not source:
        violations.append(
            CompatibilityViolation(
                check_id="CHK-1",
                rule=_REQUIRED_FIELDS_RULE,
                detail="source is empty or missing",
                event_id=eid,
            )
        )
    if payload is not None and not payload:
        violations.append(
            CompatibilityViolation(
                check_id="CHK-1",
                rule=_REQUIRED_FIELDS_RULE,
                detail="payload is an empty dict; at least one key is required",
                event_id=eid,
            )
        )

    # ------------------------------------------------------------------
    # CHK-2: Event type registered or valid custom
    # ------------------------------------------------------------------
    event_type = getattr(event, "event_type", None)
    if event_type is not None and not is_registered(str(event_type)):
        try:
            validate_custom(str(event_type))
        except EventTypeError as exc:
            violations.append(
                CompatibilityViolation(
                    check_id="CHK-2",
                    rule="Event type registered or valid custom",
                    detail=str(exc),
                    event_id=eid,
                )
            )

    # ------------------------------------------------------------------
    # CHK-3: Source conforms to name@semver pattern
    # (skip when source was empty — CHK-1 already covered it)
    # ------------------------------------------------------------------
    if source and not _SOURCE_PATTERN.match(source):
        violations.append(
            CompatibilityViolation(
                check_id="CHK-3",
                rule="Source conforms to name@semver",
                detail=f"source {source!r} does not match 'tool-name@semver' pattern",
                event_id=eid,
            )
        )

    # ------------------------------------------------------------------
    # CHK-5: Event ID is a valid 26-character ULID
    # ------------------------------------------------------------------
    if eid is not None and not _validate_ulid(eid):
        violations.append(
            CompatibilityViolation(
                check_id="CHK-5",
                rule="Event ID is a valid ULID",
                detail=f"event_id {eid!r} is not a valid 26-character ULID",
                event_id=eid,
            )
        )

    return violations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def test_compatibility(
    events: Sequence[object],  # Sequence[Event]
) -> CompatibilityResult:
    """Run all RFC-0001 compatibility checks against *events*.

    Args:
        events: Sequence of :class:`~agentobs.event.Event` objects to inspect.

    Returns:
        :class:`CompatibilityResult` with aggregated pass/fail and violation list.

    Example::

        result = test_compatibility(my_events)
        if not result:
            for v in result.violations:
                print(v.check_id, v.detail)
    """
    all_violations: list[CompatibilityViolation] = []
    event_list = list(events)
    for evt in event_list:
        all_violations.extend(_check_event(evt))

    return CompatibilityResult(
        passed=len(all_violations) == 0,
        events_checked=len(event_list),
        violations=all_violations,
    )
