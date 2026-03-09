"""agentobs.compliance.test_isolation — Tenant isolation verification.

Helps multi-tenant deployments ensure that events from separate tenants are
correctly scoped and never cross-contaminated.

Public API
----------
- :class:`IsolationViolation`    — a single isolation failure record.
- :class:`IsolationResult`       — aggregated result.
- :func:`verify_tenant_isolation` — check two tenant groups for isolation.
- :func:`verify_events_scoped`   — check that events carry the expected
                                   ``org_id`` / ``team_id``.
- :func:`_check_org_disjoint`    — internal helper (exposed for testing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "IsolationResult",
    "IsolationViolation",
    "verify_events_scoped",
    "verify_tenant_isolation",
]


@dataclass(frozen=True)
class IsolationViolation:
    """An immutable record describing a single tenant isolation failure.

    Attributes:
        event_id:       ``event_id`` of the offending event.
        violation_type: One of ``"missing_org_id"``, ``"mixed_org_ids"``, or
                        ``"shared_org_id"``, ``"wrong_org_id"``,
                        ``"wrong_team_id"``.
        detail:         Human-readable description of the failure.
    """

    event_id: str
    violation_type: str
    detail: str


@dataclass
class IsolationResult:
    """Aggregated result from isolation-verification functions.

    Attributes:
        passed:     ``True`` only when *violations* is empty.
        violations: All detected failures.
    """

    passed: bool
    violations: list[IsolationViolation] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_org_disjoint(
    group_a: Sequence[object],  # Sequence[Event]
    group_b: Sequence[object],  # Sequence[Event]
) -> list[IsolationViolation]:
    """Return violations for any ``org_id`` that appears in *both* groups.

    Events with ``org_id=None`` are excluded: a missing org_id does not itself
    constitute evidence of cross-tenant contamination.

    Args:
        group_a: First tenant's events.
        group_b: Second tenant's events.

    Returns:
        List of :class:`IsolationViolation` with
        ``violation_type="shared_org_id"`` for every event in either group
        whose org_id appears in the other group.
    """
    org_ids_a = {
        getattr(evt, "org_id", None)
        for evt in group_a
        if getattr(evt, "org_id", None) is not None
    }
    org_ids_b = {
        getattr(evt, "org_id", None)
        for evt in group_b
        if getattr(evt, "org_id", None) is not None
    }
    shared = org_ids_a & org_ids_b
    if not shared:
        return []

    violations: list[IsolationViolation] = []
    for evt in list(group_a) + list(group_b):
        eid = getattr(evt, "event_id", None) or ""
        org_id = getattr(evt, "org_id", None)
        if org_id in shared:
            violations.append(
                IsolationViolation(
                    event_id=eid,
                    violation_type="shared_org_id",
                    detail=(
                        f"org_id {org_id!r} appears in both tenant groups"
                    ),
                )
            )
    return violations


def _check_missing_org_ids(events: Sequence[object]) -> list[IsolationViolation]:
    """Return violations for events with no org_id (strict mode check)."""
    result = []
    for evt in events:
        eid = getattr(evt, "event_id", None) or ""
        if getattr(evt, "org_id", None) is None:
            result.append(IsolationViolation(
                event_id=eid,
                violation_type="missing_org_id",
                detail="org_id is None; all events must be scoped in strict mode",
            ))
    return result


def _check_mixed_org_ids(group: Sequence[object]) -> list[IsolationViolation]:
    """Return violations if a group contains events from more than one org_id."""
    result = []
    unique_org_ids = {
        getattr(evt, "org_id", None)
        for evt in group
        if getattr(evt, "org_id", None) is not None
    }
    if len(unique_org_ids) > 1:
        for evt in group:
            eid = getattr(evt, "event_id", None) or ""
            if getattr(evt, "org_id", None) is not None:
                result.append(IsolationViolation(
                    event_id=eid,
                    violation_type="mixed_org_ids",
                    detail=f"Group contains multiple org_ids: {sorted(unique_org_ids)!r}",
                ))
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_tenant_isolation(
    group_a: Sequence[object],  # Sequence[Event]
    group_b: Sequence[object],  # Sequence[Event]
    *,
    strict: bool = False,
) -> IsolationResult:
    """Verify that *group_a* and *group_b* are properly tenant-isolated.

    Checks performed:

    1. **Missing org_id** (strict mode only) — events with ``org_id=None`` are
       flagged as ``"missing_org_id"``.
    2. **Mixed org_ids** — if a single group contains events from more than one
       *org_id*, each such event is flagged as ``"mixed_org_ids"``.
    3. **Shared org_id** — if the same *org_id* appears in *both* groups, each
       event carrying that id is flagged as ``"shared_org_id"``.

    Args:
        group_a: First tenant's events.
        group_b: Second tenant's events.
        strict:  When ``True``, events with ``org_id=None`` are also flagged.

    Returns:
        :class:`IsolationResult` with all detected violations.

    Example::

        result = verify_tenant_isolation(tenant_a_events, tenant_b_events)
        if not result:
            for v in result.violations:
                print(v.violation_type, v.event_id)
    """
    violations: list[IsolationViolation] = []

    if strict:
        violations.extend(_check_missing_org_ids(list(group_a) + list(group_b)))

    for group in (group_a, group_b):
        violations.extend(_check_mixed_org_ids(group))

    violations.extend(_check_org_disjoint(group_a, group_b))

    return IsolationResult(passed=len(violations) == 0, violations=violations)


def verify_events_scoped(
    events: Sequence[object],  # Sequence[Event]
    *,
    expected_org_id: str | None = None,
    expected_team_id: str | None = None,
) -> IsolationResult:
    """Verify that every event in *events* carries the expected scope values.

    When both *expected_org_id* and *expected_team_id* are ``None`` (the
    default), every event trivially passes.

    Args:
        events:           Events to inspect.
        expected_org_id:  The ``org_id`` value every event must carry, or
                          ``None`` to skip the org check.
        expected_team_id: The ``team_id`` value every event must carry, or
                          ``None`` to skip the team check.

    Returns:
        :class:`IsolationResult` with ``"wrong_org_id"`` / ``"wrong_team_id"``
        violations.

    Example::

        result = verify_events_scoped(events, expected_org_id="org-123")
        assert result.passed
    """
    violations: list[IsolationViolation] = []

    for evt in events:
        eid = getattr(evt, "event_id", None) or ""

        if expected_org_id is not None:
            actual_org = getattr(evt, "org_id", None)
            if actual_org != expected_org_id:
                violations.append(
                    IsolationViolation(
                        event_id=eid,
                        violation_type="wrong_org_id",
                        detail=(
                            f"expected org_id {expected_org_id!r}, "
                            f"got {actual_org!r}"
                        ),
                    )
                )

        if expected_team_id is not None:
            actual_team = getattr(evt, "team_id", None)
            if actual_team != expected_team_id:
                violations.append(
                    IsolationViolation(
                        event_id=eid,
                        violation_type="wrong_team_id",
                        detail=(
                            f"expected team_id {expected_team_id!r}, "
                            f"got {actual_team!r}"
                        ),
                    )
                )

    return IsolationResult(passed=len(violations) == 0, violations=violations)
