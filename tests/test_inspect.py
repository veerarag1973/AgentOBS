"""Tests for agentobs.inspect — Tool 3: Tool Call Inspector.

Covers:
- ToolCallRecord creation and __str__
- InspectorSession.attach() (global + scoped to run)
- Hook-based span capture
- args extracted from arg.* attributes
- result extracted from return_value attribute
- duration_ms, span_id, trace_id, status, error fields
- was_result_used heuristic (True / False / None)
- detach() stops capture
- reset() clears state
- summary() / __repr__ output
- inspect_trace() from JSONL
- inspect_trace() with trace_id filter
- Public API accessible from agentobs namespace
- @trace(tool=True) auto-captures args and return_value
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import agentobs
from agentobs._span import Span
from agentobs.inspect import (
    InspectorSession,
    ToolCallRecord,
    inspect_trace,
    _is_tool_span,
    _check_result_used,
)
from agentobs.trace import trace
from agentobs.testing import capture_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span(
    name: str = "do_lookup",
    operation: str = "execute_tool",
    attributes: dict | None = None,
    status: str = "ok",
    error: str | None = None,
    span_id: str = "abcd1234abcd1234",
    trace_id: str = "a" * 32,
    start_ns: int | None = None,
) -> Span:
    s = Span(name=name, operation=operation, attributes=attributes or {})
    s.span_id = span_id
    s.trace_id = trace_id
    s.status = status
    s.error = error
    s.start_ns = start_ns or (int(time.time() * 1e9))
    s.end_ns = s.start_ns + 50_000_000  # 50 ms
    s.duration_ms = 50.0
    return s


def _tool_span(
    name: str = "do_lookup",
    args: dict | None = None,
    result: str | None = "some_result",
    **kwargs,
) -> Span:
    attrs: dict = {}
    attrs["tool"] = True
    if args:
        for k, v in args.items():
            attrs[f"arg.{k}"] = v
    if result is not None:
        attrs["return_value"] = result
    return _span(name=name, operation="execute_tool", attributes=attrs, **kwargs)


def _model_span(name: str = "llm-call", attrs: dict | None = None, **kwargs) -> Span:
    return _span(name=name, operation="chat", attributes=attrs or {}, **kwargs)


# ---------------------------------------------------------------------------
# ToolCallRecord
# ---------------------------------------------------------------------------


class TestToolCallRecord:
    def test_creation(self):
        r = ToolCallRecord(
            name="get_weather",
            args={"city": "London"},
            result="sunny, 22°C",
            duration_ms=35.7,
            span_id="abc",
            trace_id="def",
            timestamp=1700000000.0,
            status="ok",
            error=None,
        )
        assert r.name == "get_weather"
        assert r.args == {"city": "London"}
        assert r.result == "sunny, 22°C"
        assert r.duration_ms == pytest.approx(35.7)
        assert r.was_result_used is None

    def test_frozen(self):
        r = ToolCallRecord(
            name="x", args={}, result=None, duration_ms=None,
            span_id="s", trace_id="t", timestamp=0.0, status="ok", error=None,
        )
        with pytest.raises((AttributeError, TypeError)):
            r.name = "y"  # type: ignore[misc]

    def test_str_ok(self):
        r = ToolCallRecord(
            name="search", args={}, result="data", duration_ms=12.5,
            span_id="s", trace_id="t", timestamp=0.0, status="ok", error=None,
            was_result_used=True,
        )
        s = str(r)
        assert "search" in s
        assert "12.5ms" in s
        assert "used" in s

    def test_str_error(self):
        r = ToolCallRecord(
            name="fail_tool", args={}, result=None, duration_ms=5.0,
            span_id="s", trace_id="t", timestamp=0.0, status="error",
            error="timeout",
        )
        s = str(r)
        assert "error" in s.lower()
        assert "timeout" in s

    def test_str_discarded(self):
        r = ToolCallRecord(
            name="search", args={}, result="ok", duration_ms=1.0,
            span_id="s", trace_id="t", timestamp=0.0, status="ok", error=None,
            was_result_used=False,
        )
        assert "discarded" in str(r)

    def test_str_unknown_used(self):
        r = ToolCallRecord(
            name="x", args={}, result=None, duration_ms=None,
            span_id="s", trace_id="t", timestamp=0.0, status="ok", error=None,
            was_result_used=None,
        )
        assert "unknown" in str(r)


# ---------------------------------------------------------------------------
# _is_tool_span
# ---------------------------------------------------------------------------


class TestIsToolSpan:
    def test_execute_tool_operation(self):
        s = _span(operation="execute_tool")
        assert _is_tool_span(s) is True

    def test_tool_call_operation(self):
        s = _span(operation="tool_call")
        assert _is_tool_span(s) is True

    def test_tool_attribute(self):
        s = _span(operation="chat", attributes={"tool": True})
        assert _is_tool_span(s) is True

    def test_chat_not_tool(self):
        s = _span(operation="chat")
        assert _is_tool_span(s) is False

    def test_tool_attribute_false(self):
        s = _span(operation="chat", attributes={"tool": False})
        assert _is_tool_span(s) is False


# ---------------------------------------------------------------------------
# _check_result_used heuristic
# ---------------------------------------------------------------------------


class TestCheckResultUsed:
    def test_no_return_value(self):
        tool = _span(operation="execute_tool", attributes={})
        subsequent = [_model_span(attrs={"arg.messages": "some context"})]
        assert _check_result_used(tool, subsequent) is None

    def test_result_found_in_subsequent(self):
        result = "weather_data_here"
        tool = _span(operation="execute_tool", attributes={"return_value": result})
        # subsequent span contains the result
        sub = _model_span(attrs={"arg.prompt": f"Based on: {result}"})
        assert _check_result_used(tool, [sub]) is True

    def test_result_not_found_in_subsequent(self):
        result = "weather_data_here"
        tool = _span(operation="execute_tool", attributes={"return_value": result})
        sub = _model_span(attrs={"arg.prompt": "Tell me a joke"})
        assert _check_result_used(tool, [sub]) is False

    def test_no_subsequent_spans(self):
        result = "some_data"
        tool = _span(operation="execute_tool", attributes={"return_value": result})
        assert _check_result_used(tool, []) is None

    def test_none_result_str(self):
        """Return value of literal string 'None' → indeterminate."""
        tool = _span(operation="execute_tool", attributes={"return_value": "None"})
        sub = _model_span(attrs={"arg.ctx": "None present here"})
        assert _check_result_used(tool, [sub]) is None

    def test_unrepresentable_result(self):
        tool = _span(operation="execute_tool", attributes={"return_value": "<unrepresentable>"})
        sub = _model_span(attrs={"arg.ctx": "<unrepresentable>"})
        assert _check_result_used(tool, [sub]) is None

    def test_empty_result(self):
        tool = _span(operation="execute_tool", attributes={"return_value": ""})
        sub = _model_span(attrs={"arg.ctx": "hello"})
        assert _check_result_used(tool, [sub]) is None


# ---------------------------------------------------------------------------
# InspectorSession — basic capture
# ---------------------------------------------------------------------------


class TestInspectorSessionCapture:
    def _fire_span(self, session: InspectorSession, span: Span) -> None:
        """Simulate the on_span_end hook firing for a span."""
        session._on_span_end(span)

    def test_tool_call_captured(self):
        session = InspectorSession()
        session._active = True
        s = _tool_span(name="search", result="data")
        self._fire_span(session, s)
        calls = session.tool_calls
        assert len(calls) == 1
        assert calls[0].name == "search"

    def test_non_tool_span_not_in_tool_calls(self):
        session = InspectorSession()
        session._active = True
        # Fire a non-tool span
        model = _model_span()
        self._fire_span(session, model)
        # Non-tool spans are stored internally (they help the heuristic) but
        # not returned from tool_calls.
        assert len(session.tool_calls) == 0

    def test_all_span_count_includes_model_spans(self):
        session = InspectorSession()
        session._active = True
        self._fire_span(session, _tool_span())
        self._fire_span(session, _model_span())
        assert session.all_span_count == 2

    def test_args_extracted(self):
        session = InspectorSession()
        session._active = True
        s = _tool_span(name="lookup", args={"query": "Paris", "limit": "10"})
        self._fire_span(session, s)
        calls = session.tool_calls
        assert calls[0].args == {"query": "Paris", "limit": "10"}

    def test_result_extracted(self):
        session = InspectorSession()
        session._active = True
        s = _tool_span(name="weather", result="sunny")
        self._fire_span(session, s)
        assert session.tool_calls[0].result == "sunny"

    def test_result_none_when_not_captured(self):
        session = InspectorSession()
        session._active = True
        s = _tool_span(name="weather", result=None)
        self._fire_span(session, s)
        assert session.tool_calls[0].result is None

    def test_duration_ms(self):
        session = InspectorSession()
        session._active = True
        s = _tool_span()
        s.duration_ms = 99.9
        self._fire_span(session, s)
        assert session.tool_calls[0].duration_ms == pytest.approx(99.9)

    def test_status_error(self):
        session = InspectorSession()
        session._active = True
        s = _tool_span(status="error", error="connection refused")
        self._fire_span(session, s)
        r = session.tool_calls[0]
        assert r.status == "error"
        assert r.error == "connection refused"

    def test_span_id_and_trace_id(self):
        session = InspectorSession()
        session._active = True
        s = _tool_span(
            span_id="deadbeef12345678",
            trace_id="cafebabe" * 4,
        )
        self._fire_span(session, s)
        r = session.tool_calls[0]
        assert r.span_id == "deadbeef12345678"
        assert r.trace_id == "cafebabe" * 4

    def test_timestamp_derived_from_start_ns(self):
        session = InspectorSession()
        session._active = True
        ns = 1_700_000_000_000_000_000
        s = _tool_span(start_ns=ns)
        self._fire_span(session, s)
        assert abs(session.tool_calls[0].timestamp - ns / 1e9) < 0.001

    def test_multiple_tool_spans_in_order(self):
        session = InspectorSession()
        session._active = True
        for name in ("first_tool", "second_tool", "third_tool"):
            self._fire_span(session, _tool_span(name=name))
        calls = session.tool_calls
        assert [c.name for c in calls] == ["first_tool", "second_tool", "third_tool"]


# ---------------------------------------------------------------------------
# InspectorSession — was_result_used heuristic
# ---------------------------------------------------------------------------


class TestWasResultUsed:
    def _fire(self, session: InspectorSession, span: Span) -> None:
        session._on_span_end(span)

    def test_result_used_true(self):
        session = InspectorSession()
        session._active = True
        result = "WEATHER_DATA_42"
        self._fire(session, _tool_span(result=result))
        self._fire(session, _model_span(attrs={"arg.prompt": f"context: {result}"}))
        calls = session.tool_calls
        assert calls[0].was_result_used is True

    def test_result_used_false(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(result="WEATHER_DATA_42"))
        self._fire(session, _model_span(attrs={"arg.prompt": "Tell me a joke"}))
        calls = session.tool_calls
        assert calls[0].was_result_used is False

    def test_result_used_none_no_subsequent(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(result="some_data"))
        calls = session.tool_calls
        assert calls[0].was_result_used is None

    def test_result_used_none_no_result(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(result=None))
        self._fire(session, _model_span(attrs={"arg.prompt": "hello"}))
        calls = session.tool_calls
        assert calls[0].was_result_used is None

    def test_second_tool_usage_tracked_independently(self):
        """Result used flags are per-tool-span; unused results are detected."""
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(name="tool_a", result="RESULT_A"))
        self._fire(session, _tool_span(name="tool_b", result="RESULT_B"))
        # LLM uses only tool_b result — RESULT_A is discarded
        self._fire(session, _model_span(attrs={"arg.prompt": "data: RESULT_B"}))
        calls = session.tool_calls
        assert calls[0].was_result_used is False  # RESULT_A not found in subsequent spans
        assert calls[1].was_result_used is True   # RESULT_B found in model span


# ---------------------------------------------------------------------------
# InspectorSession — detach / reset
# ---------------------------------------------------------------------------


class TestDetachReset:
    def _fire(self, session: InspectorSession, span: Span) -> None:
        session._on_span_end(span)

    def test_detach_stops_capture(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(name="first"))
        session.detach()
        self._fire(session, _tool_span(name="second"))  # should be ignored
        assert len(session.tool_calls) == 1
        assert session.tool_calls[0].name == "first"

    def test_reset_clears_spans(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span())
        assert len(session.tool_calls) == 1
        session.reset()
        assert len(session.tool_calls) == 0

    def test_reset_re_enables(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(name="before"))
        session.detach()
        session.reset()
        self._fire(session, _tool_span(name="after"))
        calls = session.tool_calls
        assert len(calls) == 1
        assert calls[0].name == "after"

    def test_detach_returns_self(self):
        session = InspectorSession()
        session._active = True
        assert session.detach() is session

    def test_reset_returns_self(self):
        session = InspectorSession()
        assert session.reset() is session


# ---------------------------------------------------------------------------
# InspectorSession — trace_id scoping
# ---------------------------------------------------------------------------


class TestTraceIdScoping:
    def _fire(self, session: InspectorSession, span: Span) -> None:
        session._on_span_end(span)

    def test_spans_filtered_by_trace_id(self):
        session = InspectorSession()
        session._active = True
        session._trace_id_filter = "target_trace_" + "0" * 18  # 32 chars

        matching = _tool_span(name="match")
        matching.trace_id = session._trace_id_filter
        self._fire(session, matching)

        other = _tool_span(name="other")
        other.trace_id = "other_trace_" + "0" * 20
        self._fire(session, other)

        calls = session.tool_calls
        assert len(calls) == 1
        assert calls[0].name == "match"

    def test_no_filter_captures_all(self):
        session = InspectorSession()
        session._active = True
        session._trace_id_filter = None

        for tid in ("trace_aaa" + "0" * 23, "trace_bbb" + "0" * 23):
            s = _tool_span()
            s.trace_id = tid
            self._fire(session, s)
        assert len(session.tool_calls) == 2


# ---------------------------------------------------------------------------
# InspectorSession — summary / repr
# ---------------------------------------------------------------------------


class TestSummary:
    def _fire(self, session: InspectorSession, span: Span) -> None:
        session._on_span_end(span)

    def test_summary_no_calls(self):
        session = InspectorSession()
        summary = session.summary()
        assert "No tool calls" in summary

    def test_summary_shows_tool_names(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(name="search_web"))
        self._fire(session, _tool_span(name="read_file"))
        summary = session.summary()
        assert "search_web" in summary
        assert "read_file" in summary

    def test_summary_shows_duration(self):
        session = InspectorSession()
        session._active = True
        s = _tool_span()
        s.duration_ms = 123.4
        self._fire(session, s)
        summary = session.summary()
        assert "123.4" in summary

    def test_summary_shows_status(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(status="error", error="boom"))
        summary = session.summary()
        assert "error" in summary

    def test_repr_equals_summary(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span(name="test_tool"))
        assert repr(session) == session.summary()

    def test_len(self):
        session = InspectorSession()
        session._active = True
        for _ in range(3):
            self._fire(session, _tool_span())
        assert len(session) == 3

    def test_total_in_summary(self):
        session = InspectorSession()
        session._active = True
        self._fire(session, _tool_span())
        self._fire(session, _tool_span())
        summary = session.summary()
        assert "2" in summary


# ---------------------------------------------------------------------------
# InspectorSession.attach() integration
# ---------------------------------------------------------------------------


class TestAttach:
    def test_attach_registers_hook_globally(self):
        """attach() without run captures spans from any trace_id."""
        session = InspectorSession()
        session.attach()
        try:
            # Simulate a span ending through the session's own callback
            s = _tool_span(name="hook_driven")
            session._on_span_end(s)
            assert len(session.tool_calls) == 1
        finally:
            session.detach()

    def test_attach_with_run_sets_trace_filter(self):
        """attach(run) scopes to run.trace_id."""
        class FakeRun:
            trace_id = "feedface" * 4

        session = InspectorSession()
        session.attach(FakeRun())
        assert session._trace_id_filter == "feedface" * 4
        session.detach()

    def test_attach_returns_self(self):
        session = InspectorSession()
        result = session.attach()
        assert result is session
        session.detach()


# ---------------------------------------------------------------------------
# @trace(tool=True) integration
# ---------------------------------------------------------------------------


class TestTraceToolDecorator:
    """Tests that @trace(tool=True) auto-captures args, return_value and tool marker."""

    def _capture_spans(self):
        from agentobs._hooks import hooks
        captured: list = []
        hooks.on_span_end(captured.append)
        return captured, hooks

    def _remove_hook(self, hooks, captured):
        with hooks._lock:
            try:
                hooks._all_end_hooks.remove(captured.append)
            except ValueError:
                pass

    def test_auto_captures_args(self):
        """@trace(tool=True) records arg.* without explicit capture_args=True."""
        from agentobs._hooks import hooks

        captured: list = []
        hooks.on_span_end(captured.append)
        try:
            @trace(tool=True)
            def lookup(query: str, limit: int = 10) -> str:
                return f"result_for_{query}"

            lookup("hello", limit=5)
        finally:
            self._remove_hook(hooks, captured)

        span = next(s for s in captured if getattr(s, "name", "").endswith("lookup"))
        attrs = getattr(span, "attributes", {})
        assert "arg.query" in attrs
        assert attrs["arg.query"] == "'hello'"
        assert "arg.limit" in attrs
        assert attrs["arg.limit"] == "5"

    def test_auto_captures_return_value(self):
        """@trace(tool=True) records return_value automatically."""
        from agentobs._hooks import hooks

        captured: list = []
        hooks.on_span_end(captured.append)
        try:
            @trace(tool=True)
            def search(q: str) -> str:
                return "found_42"

            search("what")
        finally:
            self._remove_hook(hooks, captured)

        span = next(s for s in captured if getattr(s, "name", "").endswith("search"))
        attrs = getattr(span, "attributes", {})
        assert attrs.get("return_value") == "'found_42'"

    def test_tool_marker_attribute(self):
        """@trace(tool=True) sets span.attributes['tool'] = True."""
        from agentobs._hooks import hooks

        captured: list = []
        hooks.on_span_end(captured.append)
        try:
            @trace(tool=True)
            def my_tool(x: int) -> int:
                return x * 2

            my_tool(3)
        finally:
            self._remove_hook(hooks, captured)

        span = next(s for s in captured if getattr(s, "name", "").endswith("my_tool"))
        attrs = getattr(span, "attributes", {})
        assert attrs.get("tool") is True

    def test_operation_is_execute_tool(self):
        """@trace(tool=True) emits span with operation='execute_tool'."""
        from agentobs._hooks import hooks

        captured: list = []
        hooks.on_span_end(captured.append)
        try:
            @trace(tool=True)
            def find(x: str) -> str:
                return x

            find("a")
        finally:
            self._remove_hook(hooks, captured)

        span = next(s for s in captured if getattr(s, "name", "").endswith("find"))
        assert str(getattr(span, "operation", "")) == "execute_tool"

    def test_inspector_session_captures_trace_tool_spans(self):
        """Full integration: @trace(tool=True) spans are captured by InspectorSession."""
        session = InspectorSession()
        session.attach()
        try:
            @trace(tool=True)
            def get_stock(ticker: str) -> str:
                return f"${ticker}=150.00"

            get_stock("MSFT")

            calls = session.tool_calls
            stock_calls = [c for c in calls if "get_stock" in c.name]
            assert len(stock_calls) >= 1
            call = stock_calls[0]
            assert "'MSFT'" in str(call.args)
            assert "'$MSFT=150.00'" in str(call.result)
        finally:
            session.detach()

    def test_async_tool_auto_captures(self):
        """@trace(tool=True) works on async functions too."""
        import asyncio
        from agentobs._hooks import hooks

        captured: list = []
        hooks.on_span_end(captured.append)
        try:
            @trace(tool=True)
            async def async_lookup(q: str) -> str:
                return f"async_result_{q}"

            asyncio.run(async_lookup("test"))
        finally:
            self._remove_hook(hooks, captured)

        span = next(s for s in captured if getattr(s, "name", "").endswith("async_lookup"))
        attrs = getattr(span, "attributes", {})
        assert "arg.q" in attrs
        assert "return_value" in attrs


# ---------------------------------------------------------------------------
# inspect_trace() — JSONL replay
# ---------------------------------------------------------------------------


def _write_event_jsonl(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _tool_event(
    name: str = "search",
    trace_id: str = "a" * 32,
    span_id: str = "b" * 16,
    result: str | None = "found_it",
    args: dict | None = None,
    status: str = "ok",
    error: str | None = None,
    start_ns: int = 1_700_000_000_000_000_000,
    duration_ms: float = 25.0,
) -> dict:
    attributes: dict = {"tool": True}
    if args:
        for k, v in args.items():
            attributes[f"arg.{k}"] = v
    if result is not None:
        attributes["return_value"] = result
    return {
        "schema_version": "2.0",
        "event_id": "01AAAAAAAAAAAAAAAAAAAAAAAAA",
        "event_type": "llm.trace.span.completed",
        "source": "test-service@0.0.0",
        "timestamp": "2024-01-01T00:00:00.000000Z",
        "span_id": span_id,
        "trace_id": trace_id,
        "payload": {
            "span_name": name,
            "span_id": span_id,
            "trace_id": trace_id,
            "span_kind": "CLIENT",
            "operation": "execute_tool",
            "status": status,
            "error": error,
            "start_time_unix_nano": start_ns,
            "end_time_unix_nano": start_ns + int(duration_ms * 1_000_000),
            "duration_ms": duration_ms,
            "attributes": attributes,
            "tool_calls": [],
            "reasoning_steps": [],
        },
    }


def _model_event(
    name: str = "llm-call",
    trace_id: str = "a" * 32,
    attrs: dict | None = None,
) -> dict:
    span_id = "c" * 16
    return {
        "schema_version": "2.0",
        "event_id": "01BBBBBBBBBBBBBBBBBBBBBBBBB",
        "event_type": "llm.trace.span.completed",
        "source": "test-service@0.0.0",
        "timestamp": "2024-01-01T00:00:01.000000Z",
        "span_id": span_id,
        "trace_id": trace_id,
        "payload": {
            "span_name": name,
            "span_id": span_id,
            "trace_id": trace_id,
            "span_kind": "CLIENT",
            "operation": "chat",
            "status": "ok",
            "error": None,
            "start_time_unix_nano": 1_700_000_001_000_000_000,
            "end_time_unix_nano": 1_700_000_001_300_000_000,
            "duration_ms": 300.0,
            "attributes": attrs or {},
            "tool_calls": [],
            "reasoning_steps": [],
        },
    }


class TestInspectTrace:
    def test_basic_reconstruction(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        _write_event_jsonl(p, [_tool_event(name="find_docs")])
        records = inspect_trace(str(p))
        assert len(records) == 1
        assert records[0].name == "find_docs"

    def test_args_extracted(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        _write_event_jsonl(p, [_tool_event(args={"query": "London", "n": "5"})])
        records = inspect_trace(str(p))
        assert records[0].args == {"query": "London", "n": "5"}

    def test_result_extracted(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        _write_event_jsonl(p, [_tool_event(result="big_data")])
        records = inspect_trace(str(p))
        assert records[0].result == "big_data"

    def test_duration_ms(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        _write_event_jsonl(p, [_tool_event(duration_ms=88.8)])
        records = inspect_trace(str(p))
        assert records[0].duration_ms == pytest.approx(88.8)

    def test_status_and_error(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        _write_event_jsonl(p, [_tool_event(status="error", error="timeout")])
        records = inspect_trace(str(p))
        assert records[0].status == "error"
        assert records[0].error == "timeout"

    def test_trace_id_filter(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        target = "aaaa" * 8
        other = "bbbb" * 8
        _write_event_jsonl(p, [
            _tool_event(name="match", trace_id=target),
            _tool_event(name="no_match", trace_id=other),
        ])
        records = inspect_trace(str(p), trace_id=target)
        assert len(records) == 1
        assert records[0].name == "match"
        assert records[0].trace_id == target

    def test_non_tool_spans_excluded(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        _write_event_jsonl(p, [_model_event(), _tool_event(name="only_this")])
        records = inspect_trace(str(p))
        assert len(records) == 1
        assert records[0].name == "only_this"

    def test_was_result_used_heuristic(self, tmp_path: Path):
        result_val = "UNIQUE_DATA_42"
        p = tmp_path / "events.jsonl"
        _write_event_jsonl(p, [
            _tool_event(result=result_val),
            _model_event(attrs={"arg.prompt": f"context: {result_val}"}),
        ])
        records = inspect_trace(str(p))
        assert records[0].was_result_used is True

    def test_was_result_discarded(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        _write_event_jsonl(p, [
            _tool_event(result="UNIQUE_DATA_99"),
            _model_event(attrs={"arg.prompt": "Tell me a joke"}),
        ])
        records = inspect_trace(str(p))
        assert records[0].was_result_used is False

    def test_skip_errors_malformed_line(self, tmp_path: Path):
        p = tmp_path / "events.jsonl"
        with p.open("w") as f:
            f.write("NOT_JSON\n")
            f.write(json.dumps(_tool_event(name="valid_tool")) + "\n")
        # With skip_errors=True should return records for valid lines only
        records = inspect_trace(str(p), skip_errors=True)
        assert any(r.name == "valid_tool" for r in records)

    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        records = inspect_trace(str(p))
        assert records == []

    def test_non_span_events_ignored(self, tmp_path: Path):
        """Events with unrelated event_type should be skipped."""
        p = tmp_path / "events.jsonl"
        non_span = {
            "schema_version": "2.0",
            "event_id": "01CCCCCCCCCCCCCCCCCCCCCCCCC",
            "event_type": "llm.cost.token_recorded",
            "source": "test-service@0.0.0",
            "timestamp": "2024-01-01T00:00:00.000000Z",
            "span_id": "dddddddddddddddd",
            "trace_id": "d" * 32,
            "payload": {"span_id": "dddddddddddddddd", "total_cost_usd": 0.01},
        }
        with p.open("w") as f:
            f.write(json.dumps(non_span) + "\n")
            f.write(json.dumps(_tool_event(name="real_tool")) + "\n")
        records = inspect_trace(str(p))
        assert len(records) == 1
        assert records[0].name == "real_tool"

    def test_span_failed_event_type(self, tmp_path: Path):
        """llm.trace.span.failed events are also included."""
        p = tmp_path / "events.jsonl"
        event = _tool_event(name="failed_tool", status="error", error="boom")
        event["event_type"] = "llm.trace.span.failed"
        event["payload"]["status"] = "error"
        _write_event_jsonl(p, [event])
        records = inspect_trace(str(p))
        assert len(records) == 1
        assert records[0].status == "error"


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


class TestPublicAPI:
    def test_inspector_session_accessible(self):
        assert hasattr(agentobs, "InspectorSession")
        assert agentobs.InspectorSession is InspectorSession

    def test_tool_call_record_accessible(self):
        assert hasattr(agentobs, "ToolCallRecord")
        assert agentobs.ToolCallRecord is ToolCallRecord

    def test_inspect_trace_accessible(self):
        assert hasattr(agentobs, "inspect_trace")
        assert agentobs.inspect_trace is inspect_trace

    def test_in_all(self):
        for name in ("InspectorSession", "ToolCallRecord", "inspect_trace"):
            assert name in agentobs.__all__, f"{name!r} missing from __all__"


# ---------------------------------------------------------------------------
# _hooks.py _TOOL_OPERATIONS bugfix
# ---------------------------------------------------------------------------


class TestHooksToolOperations:
    def test_execute_tool_in_tool_operations(self):
        from agentobs._hooks import _TOOL_OPERATIONS
        assert "execute_tool" in _TOOL_OPERATIONS

    def test_tool_call_still_in_tool_operations(self):
        from agentobs._hooks import _TOOL_OPERATIONS
        assert "tool_call" in _TOOL_OPERATIONS
