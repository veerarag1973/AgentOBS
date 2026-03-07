"""Migration helpers for agentobs events.

This module provides forward-migration utilities for transforming events from
one schema version to the next.  Implementations are added incrementally in
subsequent minor releases; until then, calling these functions raises
:exc:`NotImplementedError` so that integrators can write the call-site today
and receive a working implementation when it ships.

Phase 9 adds the v2 migration roadmap: a structured description of all known
changes between v1 and v2, together with deprecation records and sunset
policies that can be queried programmatically by tooling and the CLI.

Usage
-----
::

    from agentobs.migrate import v1_to_v2, MigrationResult

    # Raises NotImplementedError in v1.x — wires up the call-site now.
    event_v2, result = v1_to_v2(event_v1)

    # Inspect the migration roadmap.
    from agentobs.migrate import v2_migration_roadmap, DeprecationRecord

    roadmap = v2_migration_roadmap()
    for record in roadmap:
        print(record.event_type, "→", record.replacement or "(removed)")
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from agentobs.event import Event

__all__: list[str] = [
    "DeprecationRecord",
    "MigrationResult",
    "SunsetPolicy",
    "assert_no_sunset_reached",
    "v2_migration_roadmap",
]


# ---------------------------------------------------------------------------
# Core migration data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationResult:
    """Metadata about a completed migration operation.

    Attributes:
        source_version: The schema version the event was migrated *from*.
        target_version: The schema version the event was migrated *to*.
        event_id: The ``event_id`` of the event that was transformed.
        success: ``True`` when the migration completed without errors.
        transformed_fields: Names of the event fields that were modified.
        warnings: Any non-fatal issues encountered during migration.
    """

    source_version: str
    target_version: str
    event_id: str
    success: bool
    transformed_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Phase 9: Deprecation records and sunset policy
# ---------------------------------------------------------------------------


class SunsetPolicy(str, Enum):
    """Defines when a deprecated event type will be removed.

    Attributes:
        NEXT_MAJOR:   Removed in the next major version (default for breaking).
        NEXT_MINOR:   Removed in the next minor release.
        LONG_TERM:    Multi-major support window; removal deferred two+ majors.
        UNSCHEDULED:  No concrete removal date yet.
    """

    NEXT_MAJOR = "next_major"
    NEXT_MINOR = "next_minor"
    LONG_TERM = "long_term"
    UNSCHEDULED = "unscheduled"


@dataclass(frozen=True)
class DeprecationRecord:
    """A single entry in the v1 → v2 migration roadmap.

    Describes one event type that is being changed, renamed, or removed as
    part of the v2 schema revision.

    Attributes:
        event_type:   The v1 event type string.
        since:        Schema version when deprecation was announced (e.g. ``"1.1"``).
        sunset:       Schema version when the type will be removed (e.g. ``"2.0"``).
        sunset_policy: Categorised removal timeline (:class:`SunsetPolicy`).
        replacement:  The recommended v2 event type, or ``None`` if removed
                      without replacement.
        migration_notes: Free-form guidance for integrators updating to v2.
        field_renames:   Dict of ``{old_field: new_field}`` payload renames.
    """

    event_type: str
    since: str
    sunset: str
    sunset_policy: SunsetPolicy = SunsetPolicy.NEXT_MAJOR
    replacement: str | None = None
    migration_notes: str | None = None
    field_renames: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure field_renames is immutable even though frozen=True only
        # prevents re-assignment (not mutation of the contained dict).
        object.__setattr__(self, "field_renames", MappingProxyType(dict(self.field_renames)))

    def summary(self) -> str:
        """Return a one-line human-readable summary.

        Returns:
            String describing the deprecation in human-readable form.
        """
        arrow = f" → {self.replacement!r}" if self.replacement else " (removed)"
        return f"[{self.since}→{self.sunset}] {self.event_type!r}{arrow}"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"DeprecationRecord(event_type={self.event_type!r}, "
            f"since={self.since!r}, sunset={self.sunset!r})"
        )


# ---------------------------------------------------------------------------
# v2 migration roadmap
# ---------------------------------------------------------------------------


def v2_migration_roadmap() -> list[DeprecationRecord]:
    """Return the complete list of known v1 → v2 migration changes.

    This roadmap captures every event type that will be renamed, restructured,
    or removed when v2.0 ships.  It is intended to be consumed by:

    * The ``agentobs migration-roadmap`` CLI subcommand.
    * Governance tooling that warns operators before sunset.
    * Documentation generators.

    Returns:
        A list of :class:`DeprecationRecord` objects, one per deprecated
        event type, sorted alphabetically by ``event_type``.

    Note:
        This list will grow as the v2 schema design matures.  Entries marked
        ``SunsetPolicy.UNSCHEDULED`` are candidates for breaking changes but
        have no confirmed removal timeline yet.
    """
    records: list[DeprecationRecord] = [
        # --- Trace namespace ---
        DeprecationRecord(
            event_type="llm.trace.span.started",
            since="1.1",
            sunset="2.0",
            sunset_policy=SunsetPolicy.NEXT_MAJOR,
            replacement="llm.trace.span.span_started",
            migration_notes=(
                "The 'started' suffix is being normalised to 'span_started' for "
                "consistency with OpenTelemetry span lifecycle naming."
            ),
        ),
        DeprecationRecord(
            event_type="llm.trace.span.completed",
            since="1.1",
            sunset="2.0",
            sunset_policy=SunsetPolicy.NEXT_MAJOR,
            replacement="llm.trace.span.span_completed",
            migration_notes="Same normalisation as llm.trace.span.started.",
        ),
        # --- Eval namespace ---
        DeprecationRecord(
            event_type="llm.eval.score",
            since="1.1",
            sunset="2.0",
            sunset_policy=SunsetPolicy.NEXT_MAJOR,
            replacement="llm.eval.result.scored",
            field_renames={"score": "result_score", "label": "result_label"},
            migration_notes=(
                "Eval events are moving to a 'result' sub-namespace to group "
                "all evaluation outcomes. Rename payload fields as described."
            ),
        ),
        DeprecationRecord(
            event_type="llm.eval.feedback",
            since="1.1",
            sunset="2.0",
            sunset_policy=SunsetPolicy.NEXT_MAJOR,
            replacement="llm.eval.result.feedback",
            migration_notes="Moved into the 'result' sub-namespace.",
        ),
        # --- Guard namespace ---
        DeprecationRecord(
            event_type="llm.guard.input_check",
            since="1.1",
            sunset="2.0",
            sunset_policy=SunsetPolicy.NEXT_MAJOR,
            replacement="llm.guard.input.checked",
            migration_notes=(
                "Guard events are transitioning to a verb-based suffix "
                "('checked') for consistency with span lifecycle events."
            ),
        ),
        DeprecationRecord(
            event_type="llm.guard.output_check",
            since="1.1",
            sunset="2.0",
            sunset_policy=SunsetPolicy.NEXT_MAJOR,
            replacement="llm.guard.output.checked",
            migration_notes="Same suffix normalisation as llm.guard.input_check.",
        ),
        # --- Cost namespace ---
        DeprecationRecord(
            event_type="llm.cost.token_usage",
            since="1.1",
            sunset="2.0",
            sunset_policy=SunsetPolicy.NEXT_MAJOR,
            replacement="llm.cost.usage.tokens",
            field_renames={
                "prompt_tokens": "input_tokens",
                "completion_tokens": "output_tokens",
            },
            migration_notes=(
                "Token usage fields are being renamed to align with provider "
                "SDKs that use 'input'/'output' terminology (e.g. Anthropic)."
            ),
        ),
        # --- Cache namespace ---
        DeprecationRecord(
            event_type="llm.cache.hit",
            since="1.1",
            sunset="3.0",
            sunset_policy=SunsetPolicy.LONG_TERM,
            replacement="llm.cache.lookup.hit",
            migration_notes=(
                "Cache event hierarchy is being expanded in v2 but the old "
                "top-level 'hit'/'miss' events will remain valid until v3."
            ),
        ),
        DeprecationRecord(
            event_type="llm.cache.miss",
            since="1.1",
            sunset="3.0",
            sunset_policy=SunsetPolicy.LONG_TERM,
            replacement="llm.cache.lookup.miss",
            migration_notes="See llm.cache.hit migration notes.",
        ),
    ]

    return sorted(records, key=lambda r: r.event_type)


# ---------------------------------------------------------------------------
# Migration function (scaffold — not yet implemented)
# ---------------------------------------------------------------------------


class NotImplementedWarning(UserWarning):
    """Emitted when a scaffold function that is not yet implemented is called."""


def v1_to_v2(event: Event) -> tuple[Event, MigrationResult]:  # pragma: no cover
    """Migrate a v1.0 event to the v2.0 schema.

    .. deprecated::
        This function is **not yet implemented** and raises
        :exc:`NotImplementedError`.  It exists for forward-compatible
        call-site stubs only.  Use :func:`v2_migration_roadmap` to inspect
        all planned v1→v2 changes and :func:`assert_no_sunset_reached` in CI
        to catch overdue deprecations.

    Args:
        event: A v1.x :class:`~agentobs.event.Event` to migrate.

    Returns:
        A ``(event_v2, MigrationResult)`` tuple (when implemented).

    Raises:
        NotImplementedError: Always — v2 migration not yet implemented.
    """
    warnings.warn(
        "agentobs.v1_to_v2() is not yet implemented. "
        "Call v2_migration_roadmap() to inspect the planned migration changes. "
        "This function will be implemented in a future release.",
        stacklevel=2,
        category=NotImplementedWarning,
    )
    raise NotImplementedError(
        "v1_to_v2 is not yet implemented — "
        "use v2_migration_roadmap() to inspect planned v1→v2 changes."
    )


def assert_no_sunset_reached(current_version: str) -> None:
    """Raise :exc:`AssertionError` if any deprecated event type has already
    reached its announced sunset version.

    Intended for use in CI pipelines to automatically catch soft-obsolete
    deprecation records before they accumulate unchecked.

    Args:
        current_version: The SDK version string to compare against
                         (e.g. ``"2.1.0"``).

    Raises:
        AssertionError: Lists every deprecation record whose ``sunset``
                        version is ≤ *current_version*.

    Example::

        from agentobs.migrate import assert_no_sunset_reached
        assert_no_sunset_reached("2.1.0")  # passes if no sunsets reached
    """
    overdue = [
        r for r in v2_migration_roadmap()
        if r.sunset and _version_le(r.sunset, current_version)
    ]
    if overdue:
        lines = "\n".join(
            f"  {r.event_type!r} (sunset={r.sunset!r}, since={r.since!r})"
            for r in overdue
        )
        raise AssertionError(
            f"{len(overdue)} deprecated event type(s) have reached their sunset "
            f"version ({current_version!r}):\n{lines}\n"
            "Remove the deprecated types or extend the sunset version."
        )


def _version_le(sunset: str, current: str) -> bool:
    """Return ``True`` if *sunset* ≤ *current* using tuple-based comparison."""
    def _parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split(".") if x.isdigit())
    return _parts(sunset) <= _parts(current)

