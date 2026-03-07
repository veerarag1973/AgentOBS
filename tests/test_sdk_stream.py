"""Tests for agentobs._stream — emit_span, emit_agent_step, emit_agent_run, _build_source.

Phase 3 SDK coverage target.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

import agentobs._stream as stream_mod
from agentobs._span import (
    AgentRunContext,
    AgentRunContextManager,
    AgentStepContext,
    AgentStepContextManager,
    Span,
    SpanContextManager,
    _run_stack,
    _run_stack_var,
    _span_stack,
    _span_stack_var,
)
from agentobs._stream import (
    _build_source,
    _reset_exporter,
    emit_agent_run,
    emit_agent_step,
    emit_span,
)
from agentobs.types import EventType

if TYPE_CHECKING:
    from agentobs.event import Event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CapturingExporter:
    """Test exporter that captures all exported events."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def export(self, event: Event) -> None:
        self.events.append(event)


def _install_exporter() -> _CapturingExporter:
    """Install a capturing exporter and return it."""
    # Close any existing cached exporter (e.g. a JSONL exporter with an open
    # file handle) before replacing it, to avoid ResourceWarning.
    _reset_exporter()
    cap = _CapturingExporter()
    stream_mod._cached_exporter = cap
    return cap


def _clean_stacks() -> None:
    _span_stack_var.set(())
    _run_stack_var.set(())


# ===========================================================================
# _build_source
# ===========================================================================


@pytest.mark.unit
class TestBuildSource:
    def test_normal_name_and_version(self) -> None:
        src = _build_source("my-service", "1.2.3")
        assert src == "my-service@1.2.3"

    def test_non_letter_start_prepends_s(self) -> None:
        src = _build_source("1service", "0.0.1")
        assert src.startswith("s")

    def test_invalid_chars_replaced_with_dash(self) -> None:
        src = _build_source("my service!", "1.0.0")
        assert "!" not in src
        assert " " not in src

    def test_bad_version_defaults_to_0_0_0(self) -> None:
        src = _build_source("svc", "not-a-version")
        assert src.endswith("@0.0.0")

    def test_valid_version_preserved(self) -> None:
        src = _build_source("svc", "2.5.0")
        assert src.endswith("@2.5.0")

    def test_empty_service_name_fallback(self) -> None:
        src = _build_source("", "1.0.0")
        # empty string starts with non-letter, so 's' is prepended
        assert src.startswith("s")


# ===========================================================================
# _reset_exporter
# ===========================================================================


@pytest.mark.unit
class TestResetExporter:
    def test_reset_clears_cached_exporter(self) -> None:
        _install_exporter()
        _reset_exporter()
        assert stream_mod._cached_exporter is None

    def test_reset_causes_rebuild_on_next_use(self) -> None:
        from agentobs.config import configure  # noqa: PLC0415
        configure(exporter="console")
        _reset_exporter()
        # Calling _active_exporter() should rebuild
        exp = stream_mod._active_exporter()
        assert exp is not None


# ===========================================================================
# emit_span
# ===========================================================================


@pytest.mark.unit
class TestEmitSpan:
    def setup_method(self) -> None:
        _clean_stacks()

    def test_emit_span_completed_on_ok_span(self) -> None:
        cap = _install_exporter()
        with SpanContextManager(name="ok-span"):
            pass
        assert len(cap.events) == 1
        assert cap.events[0].event_type == EventType.TRACE_SPAN_COMPLETED

    def test_emit_span_failed_on_error_span(self) -> None:
        cap = _install_exporter()
        try:
            with SpanContextManager(name="err-span"):
                raise ValueError("fail")  # noqa: TRY301
        except ValueError:
            pass
        assert len(cap.events) == 1
        assert cap.events[0].event_type == EventType.TRACE_SPAN_FAILED

    def test_emitted_event_has_span_id(self) -> None:
        cap = _install_exporter()
        with SpanContextManager(name="span") as span:
            pass
        event = cap.events[0]
        assert event.span_id == span.span_id

    def test_emitted_event_has_trace_id(self) -> None:
        cap = _install_exporter()
        with SpanContextManager(name="span") as span:
            pass
        event = cap.events[0]
        assert event.trace_id == span.trace_id

    def test_emitted_event_source_from_config(self) -> None:
        from agentobs.config import configure  # noqa: PLC0415
        configure(service_name="test-service", service_version="1.0.0")
        cap = _install_exporter()
        with SpanContextManager(name="span"):
            pass
        event = cap.events[0]
        assert "test-service" in event.source

    def test_emitted_event_tags_env(self) -> None:
        from agentobs.config import configure  # noqa: PLC0415
        configure(env="staging")
        cap = _install_exporter()
        with SpanContextManager(name="span"):
            pass
        event = cap.events[0]
        assert event.tags is not None
        assert event.tags["env"] == "staging"

    def test_emit_span_directly(self) -> None:
        cap = _install_exporter()
        span = Span(name="direct")
        span.end()
        emit_span(span)
        assert len(cap.events) == 1

    def test_exporter_error_does_not_propagate(self) -> None:
        """Errors in exporter surface as UserWarning (default on_export_error='warn')."""
        import pytest  # noqa: PLC0415
        broken = MagicMock()
        broken.export.side_effect = OSError("disk full")
        stream_mod._cached_exporter = broken
        # Should not raise — a UserWarning is emitted instead
        span = Span(name="safe")
        span.end()
        with pytest.warns(UserWarning, match="disk full"):
            emit_span(span)


# ===========================================================================
# emit_agent_step
# ===========================================================================


@pytest.mark.unit
class TestEmitAgentStep:
    def setup_method(self) -> None:
        _clean_stacks()

    def test_emit_agent_step_event_type(self) -> None:
        cap = _install_exporter()
        with AgentRunContextManager("agent"), AgentStepContextManager("step"):
            pass
        # Events: step event + run event
        step_events = [e for e in cap.events if e.event_type == EventType.TRACE_AGENT_STEP]
        assert len(step_events) == 1

    def test_emit_agent_step_directly(self) -> None:
        cap = _install_exporter()
        with AgentRunContextManager("agent") as run:
            ctx = AgentStepContext(
                step_name="direct",
                agent_run_id=run.agent_run_id,
                step_index=0,
            )
            ctx.end()
            emit_agent_step(ctx)
        assert any(e.event_type == EventType.TRACE_AGENT_STEP for e in cap.events)


# ===========================================================================
# emit_agent_run
# ===========================================================================


@pytest.mark.unit
class TestEmitAgentRun:
    def setup_method(self) -> None:
        _clean_stacks()

    def test_emit_agent_run_event_type(self) -> None:
        cap = _install_exporter()
        with AgentRunContextManager("agent"):
            pass
        run_events = [e for e in cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED]
        assert len(run_events) == 1

    def test_emit_agent_run_directly(self) -> None:
        cap = _install_exporter()
        ctx = AgentRunContext(agent_name="runner")
        ctx.end()
        emit_agent_run(ctx)
        assert any(e.event_type == EventType.TRACE_AGENT_COMPLETED for e in cap.events)

    def test_run_event_has_trace_id(self) -> None:
        cap = _install_exporter()
        with AgentRunContextManager("agent") as run:
            pass
        run_event = next(e for e in cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED)
        assert run_event.trace_id == run.trace_id


# ===========================================================================
# _build_exporter — config-driven exporter instantiation
# ===========================================================================


@pytest.mark.unit
class TestBuildExporter:
    def test_console_exporter_built(self) -> None:
        from agentobs.config import configure  # noqa: PLC0415
        from agentobs.exporters.console import SyncConsoleExporter  # noqa: PLC0415
        configure(exporter="console")
        _reset_exporter()
        exp = stream_mod._active_exporter()
        assert isinstance(exp, SyncConsoleExporter)

    def test_jsonl_exporter_built(self, tmp_path) -> None:
        from agentobs.config import configure  # noqa: PLC0415
        from agentobs.exporters.jsonl import SyncJSONLExporter  # noqa: PLC0415
        configure(exporter="jsonl", endpoint=str(tmp_path / "test.jsonl"))
        _reset_exporter()
        exp = stream_mod._active_exporter()
        assert isinstance(exp, SyncJSONLExporter)
        configure(exporter="console")  # restore
        _reset_exporter()

    def test_unknown_exporter_falls_back_to_console(self) -> None:
        from agentobs.config import configure  # noqa: PLC0415
        from agentobs.exporters.console import SyncConsoleExporter  # noqa: PLC0415
        configure(exporter="unknown_exporter_xyz")
        _reset_exporter()
        exp = stream_mod._active_exporter()
        assert isinstance(exp, SyncConsoleExporter)
        configure(exporter="console")
        _reset_exporter()

    def test_org_id_in_event_when_configured(self) -> None:
        from agentobs.config import configure  # noqa: PLC0415
        configure(org_id="org_stream_test")
        cap = _install_exporter()
        _clean_stacks()
        with SpanContextManager(name="org-span"):
            pass
        assert cap.events[0].org_id == "org_stream_test"
        configure(org_id=None)
