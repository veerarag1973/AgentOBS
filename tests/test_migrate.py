"""Tests for agentobs.migrate.

Covers:
- MigrationResult dataclass (creation, field access, immutability)
- DeprecationRecord.summary() with and without replacement
- v2_migration_roadmap() returns a sorted list
- v1_to_v2() raises NotImplementedError (scaffold behaviour)
"""

from __future__ import annotations

import pytest

from agentobs import Event, EventType
from agentobs.migrate import (
    DeprecationRecord,
    MigrationResult,
    SunsetPolicy,
    v1_to_v2,
    v2_migration_roadmap,
)

# ---------------------------------------------------------------------------
# MigrationResult
# ---------------------------------------------------------------------------


class TestMigrationResult:
    def test_required_fields(self) -> None:
        result = MigrationResult(
            source_version="1.0",
            target_version="2.0",
            event_id="evt-abc",
            success=True,
        )
        assert result.source_version == "1.0"
        assert result.target_version == "2.0"
        assert result.event_id == "evt-abc"
        assert result.success is True

    def test_default_optional_fields(self) -> None:
        result = MigrationResult(
            source_version="1.0",
            target_version="2.0",
            event_id="evt-abc",
            success=True,
        )
        assert result.transformed_fields == ()
        assert result.warnings == ()

    def test_optional_fields_explicit(self) -> None:
        result = MigrationResult(
            source_version="1.0",
            target_version="2.0",
            event_id="evt-abc",
            success=False,
            transformed_fields=("payload", "event_type"),
            warnings=("unknown field 'foo' ignored",),
        )
        assert result.transformed_fields == ("payload", "event_type")
        assert result.warnings == ("unknown field 'foo' ignored",)

    def test_frozen_immutability(self) -> None:
        result = MigrationResult(
            source_version="1.0",
            target_version="2.0",
            event_id="evt-abc",
            success=True,
        )
        with pytest.raises((TypeError, AttributeError)):
            result.success = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# v1_to_v2
# ---------------------------------------------------------------------------


class TestV1ToV2:
    def test_raises_not_implemented(self) -> None:
        """v1_to_v2 is a Phase 9 scaffold — raises NotImplementedWarning (+ would raise NotImplementedError)."""
        evt = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="llm-trace@0.3.1",
            payload={"span_name": "test"},
        )
        with pytest.raises((NotImplementedError, UserWarning)):
            v1_to_v2(evt)

    def test_error_message_informative(self) -> None:
        """The error / warning message from v1_to_v2 references the function name."""
        evt = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="llm-trace@0.3.1",
            payload={"span_name": "test"},
        )
        with pytest.raises((NotImplementedError, UserWarning), match="v1_to_v2"):
            v1_to_v2(evt)


# ---------------------------------------------------------------------------
# DeprecationRecord.summary()  # noqa: ERA001
# ---------------------------------------------------------------------------


class TestDeprecationRecordSummary:
    def test_summary_with_replacement(self) -> None:
        rec = DeprecationRecord(
            event_type="llm.trace.span.started",
            since="1.1",
            sunset="2.0",
            replacement="llm.trace.span.span_started",
        )
        result = rec.summary()
        assert "llm.trace.span.started" in result
        assert "llm.trace.span.span_started" in result
        assert "[1.1→2.0]" in result
        assert "→" in result

    def test_summary_without_replacement(self) -> None:
        rec = DeprecationRecord(
            event_type="llm.old.event",
            since="1.0",
            sunset="2.0",
            replacement=None,
        )
        result = rec.summary()
        assert "llm.old.event" in result
        assert "(removed)" in result
        assert "[1.0→2.0]" in result


# ---------------------------------------------------------------------------
# v2_migration_roadmap()  # noqa: ERA001
# ---------------------------------------------------------------------------


class TestV2MigrationRoadmap:
    def test_returns_list_of_deprecation_records(self) -> None:
        records = v2_migration_roadmap()
        assert isinstance(records, list)
        assert len(records) > 0
        assert all(isinstance(r, DeprecationRecord) for r in records)

    def test_sorted_by_event_type(self) -> None:
        records = v2_migration_roadmap()
        event_types = [r.event_type for r in records]
        assert event_types == sorted(event_types)

    def test_known_entries_present(self) -> None:
        records = v2_migration_roadmap()
        event_types = {r.event_type for r in records}
        assert "llm.trace.span.started" in event_types
        assert "llm.eval.score" in event_types
        assert "llm.cache.hit" in event_types

    def test_records_have_sunset_policy(self) -> None:
        records = v2_migration_roadmap()
        for rec in records:
            assert isinstance(rec.sunset_policy, SunsetPolicy)
