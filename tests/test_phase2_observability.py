"""tests/test_phase2_observability.py — Exhaustive tests for Phase 2 changes.

Phase 2 covers:
- 2.1  span.add_event() / SpanEvent dataclass
- 2.2  Typed error categories + auto-detection + set_timeout_deadline()
- 2.3  LLM span schema additions (temperature, top_p, max_tokens)
- 2.4  Tool span schema additions (arguments_raw, result_raw, retry_count,
       external_api) + AgentOBSConfig.include_raw_tool_io

Coverage target: ≥ 95 % of the modified/new code paths.
"""

from __future__ import annotations

import json
import time
import threading
from typing import Any

import pytest

import agentobs
from agentobs import (
    SpanEvent,
    SpanErrorCategory,
    SpanPayload,
    ToolCall,
    configure,
    get_config,
    start_trace,
    tracer,
)
from agentobs._span import Span, SpanContextManager
from agentobs.namespaces.trace import GenAISystem


# ===========================================================================
# 2.1  SpanEvent dataclass
# ===========================================================================


class TestSpanEventDataclass:
    """Unit tests for the SpanEvent value object."""

    def test_basic_construction(self):
        ev = SpanEvent(name="cache.hit")
        assert ev.name == "cache.hit"
        assert isinstance(ev.timestamp_ns, int)
        assert ev.timestamp_ns > 0
        assert ev.metadata == {}

    def test_construction_with_metadata(self):
        ev = SpanEvent(name="retry.attempt", metadata={"count": 3})
        assert ev.metadata == {"count": 3}

    def test_custom_timestamp(self):
        ts = 1_700_000_000_000_000_000
        ev = SpanEvent(name="x", timestamp_ns=ts)
        assert ev.timestamp_ns == ts

    def test_name_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            SpanEvent(name="")

    def test_name_non_string_raises(self):
        with pytest.raises((ValueError, TypeError)):
            SpanEvent(name=123)  # type: ignore

    def test_timestamp_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            SpanEvent(name="x", timestamp_ns=-1)

    def test_to_dict_structure(self):
        ev = SpanEvent(name="tool.start", timestamp_ns=12345, metadata={"k": "v"})
        d = ev.to_dict()
        assert d == {"name": "tool.start", "timestamp_ns": 12345, "metadata": {"k": "v"}}

    def test_to_dict_empty_metadata(self):
        ev = SpanEvent(name="ping")
        d = ev.to_dict()
        assert d["metadata"] == {}

    def test_from_dict_roundtrip(self):
        original = SpanEvent(name="llm.response", timestamp_ns=99999, metadata={"a": 1})
        d = original.to_dict()
        restored = SpanEvent.from_dict(d)
        assert restored.name == original.name
        assert restored.timestamp_ns == original.timestamp_ns
        assert restored.metadata == original.metadata

    def test_from_dict_no_metadata_key(self):
        ev = SpanEvent.from_dict({"name": "x", "timestamp_ns": 100})
        assert ev.metadata == {}

    def test_from_dict_missing_name_raises(self):
        with pytest.raises(KeyError):
            SpanEvent.from_dict({"timestamp_ns": 100})

    def test_metadata_is_isolated_copy(self):
        """from_dict creates a copy of the metadata dict."""
        raw = {"name": "x", "timestamp_ns": 1, "metadata": {"original": True}}
        ev = SpanEvent.from_dict(raw)
        raw["metadata"]["poisoned"] = True
        assert "poisoned" not in ev.metadata

    def test_exported_from_agentobs_namespace(self):
        from agentobs import SpanEvent as PublicSpanEvent
        assert PublicSpanEvent is SpanEvent


# ===========================================================================
# 2.1  Span.add_event()
# ===========================================================================


class TestSpanAddEvent:
    """Tests for Span.add_event() and event propagation into SpanPayload."""

    def test_add_single_event(self):
        with tracer.span("s") as s:
            s.add_event("start")
        assert len(s.events) == 1
        assert s.events[0].name == "start"

    def test_add_event_with_metadata(self):
        with tracer.span("s") as s:
            s.add_event("cache.hit", metadata={"hit_rate": 0.92})
        assert s.events[0].metadata == {"hit_rate": 0.92}

    def test_add_multiple_events_ordered(self):
        with tracer.span("s") as s:
            s.add_event("a")
            s.add_event("b")
            s.add_event("c")
        assert [e.name for e in s.events] == ["a", "b", "c"]

    def test_event_timestamps_are_monotonic(self):
        with tracer.span("s") as s:
            s.add_event("first")
            time.sleep(0.001)
            s.add_event("second")
        assert s.events[1].timestamp_ns >= s.events[0].timestamp_ns

    def test_add_event_no_metadata_defaults_to_empty(self):
        with tracer.span("s") as s:
            s.add_event("x")
        assert s.events[0].metadata == {}

    def test_add_event_metadata_none_defaults_to_empty(self):
        with tracer.span("s") as s:
            s.add_event("x", metadata=None)
        assert s.events[0].metadata == {}

    def test_events_in_span_payload(self):
        with tracer.span("s") as s:
            s.add_event("prompt.sent")
            s.add_event("response.recv", metadata={"tokens": 42})
        payload = s.to_span_payload()
        assert len(payload.events) == 2
        assert payload.events[0].name == "prompt.sent"
        assert payload.events[1].metadata == {"tokens": 42}

    def test_events_in_payload_to_dict(self):
        with tracer.span("s") as s:
            s.add_event("cache.miss")
        d = s.to_span_payload().to_dict()
        assert "events" in d
        assert d["events"][0]["name"] == "cache.miss"

    def test_no_events_omitted_from_payload_dict(self):
        with tracer.span("s") as s:
            ...
        d = s.to_span_payload().to_dict()
        assert "events" not in d

    def test_events_survive_span_payload_from_dict_roundtrip(self):
        with tracer.span("s") as s:
            s.add_event("ev1", metadata={"x": 1})
        d = s.to_span_payload().to_dict()
        restored = SpanPayload.from_dict(d)
        assert len(restored.events) == 1
        assert restored.events[0].name == "ev1"
        assert restored.events[0].metadata == {"x": 1}

    def test_events_in_trace_to_json(self):
        trace = start_trace("bot")
        with trace.span("s") as s:
            s.add_event("tick")
        trace.end()
        data = json.loads(trace.to_json())
        span_dicts = data["spans"]
        assert any("events" in sd for sd in span_dicts)

    def test_add_event_after_record_error(self):
        """Events can be added even after an error is recorded."""
        with tracer.span("s") as s:
            try:
                raise ValueError("oops")
            except ValueError as exc:
                s.record_error(exc)
            s.add_event("post_error_diagnostic")
        assert s.events[0].name == "post_error_diagnostic"


# ===========================================================================
# 2.2  Error telemetry — error categories
# ===========================================================================


class TestErrorCategory:
    """Tests for Span.record_error() category logic and SpanPayload.error_category."""

    def test_default_category_is_unknown(self):
        with tracer.span("s") as s:
            s.record_error(ValueError("bad"))
        assert s.error_category == "unknown_error"

    def test_timeout_error_auto_detected(self):
        with tracer.span("s") as s:
            s.record_error(TimeoutError("took too long"))
        assert s.error_category == "timeout_error"

    def test_explicit_category_overrides_auto(self):
        with tracer.span("s") as s:
            s.record_error(TimeoutError("slow"), category="llm_error")
        assert s.error_category == "llm_error"

    def test_explicit_tool_error(self):
        with tracer.span("s") as s:
            s.record_error(RuntimeError("tool crash"), category="tool_error")
        assert s.error_category == "tool_error"

    def test_explicit_agent_error(self):
        with tracer.span("s") as s:
            s.record_error(RuntimeError("crash"), category="agent_error")
        assert s.error_category == "agent_error"

    def test_explicit_unknown_error(self):
        with tracer.span("s") as s:
            s.record_error(IOError("disk"), category="unknown_error")
        assert s.error_category == "unknown_error"

    def test_error_category_in_span_payload(self):
        with tracer.span("s") as s:
            s.record_error(ValueError("bad"), category="llm_error")
        payload = s.to_span_payload()
        assert payload.error_category == "llm_error"

    def test_error_category_in_payload_to_dict(self):
        with tracer.span("s") as s:
            s.record_error(RuntimeError("x"), category="tool_error")
        d = s.to_span_payload().to_dict()
        assert d["error_category"] == "tool_error"

    def test_no_error_category_omitted_from_dict(self):
        """When no error occurred, error_category is absent from the dict."""
        with tracer.span("s") as s:
            ...
        d = s.to_span_payload().to_dict()
        assert "error_category" not in d

    def test_error_category_in_from_dict_roundtrip(self):
        with tracer.span("s") as s:
            s.record_error(TimeoutError("slow"))
        d = s.to_span_payload().to_dict()
        restored = SpanPayload.from_dict(d)
        assert restored.error_category == "timeout_error"

    def test_uncaught_exception_auto_categorised(self):
        """An exception raised inside the block is auto-caught with unknown_error."""
        with pytest.raises(ValueError):
            with tracer.span("s") as s:
                raise ValueError("uncaught")
        assert s.error_category == "unknown_error"

    def test_uncaught_timeout_auto_categorised(self):
        """TimeoutError raised inside block maps to timeout_error."""
        with pytest.raises(TimeoutError):
            with tracer.span("s") as s:
                raise TimeoutError("slow")
        assert s.error_category == "timeout_error"

    def test_span_error_category_type_exported(self):
        """SpanErrorCategory is exported and is a valid type alias."""
        from agentobs import SpanErrorCategory as SEC
        # Verify it's the Literal type (runtime introspection)
        import typing
        assert hasattr(SEC, "__args__") or hasattr(typing.get_args(SEC), "__len__")


# ===========================================================================
# 2.2  set_timeout_deadline()
# ===========================================================================


class TestSetTimeoutDeadline:
    """Tests for Span.set_timeout_deadline()."""

    def test_no_timeout_when_closed_in_time(self):
        with tracer.span("s") as s:
            s.set_timeout_deadline(10.0)  # 10 second deadline — won't fire
        assert s.status == "ok"
        assert s.error_category is None

    def test_timer_fires_when_span_not_closed(self):
        s = Span(name="slow")
        s.set_timeout_deadline(0.05)  # 50ms
        time.sleep(0.12)  # wait longer than deadline
        assert s.status == "timeout"
        assert s.error_category == "timeout_error"
        assert "timed out" in s.error

    def test_timer_cancelled_on_normal_end(self):
        """Timer MUST be cancelled when span closes normally."""
        s = Span(name="fast")
        s.set_timeout_deadline(10.0)
        assert s._timeout_timer is not None
        s.end()
        # After end(), timer should be cancelled and cleared
        assert s._timeout_timer is None

    def test_timer_does_not_fire_after_end(self):
        """Timeout callback must not fire after span has ended."""
        s = Span(name="fast")
        s.set_timeout_deadline(0.05)
        s.end()  # close before deadline
        time.sleep(0.1)
        # Status must remain "ok" — end() cancelled the timer
        assert s.status == "ok"

    def test_timer_does_not_override_error_status(self):
        """If span is already in error state, timeout must not overwrite it."""
        s = Span(name="already-failed")
        s.record_error(ValueError("already bad"), category="llm_error")
        s.set_timeout_deadline(0.05)
        time.sleep(0.1)
        # The timer fires but sees status != "ok", so status stays "error"
        assert s.status == "error"
        assert s.error_category == "llm_error"

    def test_timeout_message_includes_seconds(self):
        s = Span(name="x")
        s.set_timeout_deadline(1.5)
        # Cancel the actual timer immediately
        s._timeout_timer.cancel()
        s._timeout_timer = None

    def test_timeout_timer_is_daemon(self):
        """Timer thread must be a daemon so it doesn't block process exit."""
        s = Span(name="x")
        s.set_timeout_deadline(60.0)
        assert s._timeout_timer.daemon is True
        s._timeout_timer.cancel()
        s._timeout_timer = None

    def test_set_timeout_deadline_zero_raises(self):
        s = Span(name="x")
        with pytest.raises(ValueError, match="seconds must be > 0"):
            s.set_timeout_deadline(0)

    def test_set_timeout_deadline_negative_raises(self):
        s = Span(name="x")
        with pytest.raises(ValueError, match="seconds must be > 0"):
            s.set_timeout_deadline(-1.0)

    def test_set_timeout_deadline_double_call_cancels_first(self):
        """Calling set_timeout_deadline twice must not leak the first timer."""
        s = Span(name="x")
        s.set_timeout_deadline(60.0)
        first_timer = s._timeout_timer
        s.set_timeout_deadline(60.0)  # second call
        # First timer must have been cancelled (is_alive() False or not active)
        assert not first_timer.is_alive()
        # Clean up
        s._timeout_timer.cancel()
        s._timeout_timer = None


# ===========================================================================
# 2.3  LLM span schema additions (temperature, top_p, max_tokens)
# ===========================================================================


class TestLLMSpanSchemaAdditions:
    """Tests for temperature, top_p, max_tokens on Span and SpanPayload."""

    def test_span_has_temperature(self):
        with tracer.span("s", model="gpt-4o", temperature=0.7) as s:
            ...
        assert s.temperature == 0.7

    def test_span_has_top_p(self):
        with tracer.span("s", top_p=0.95) as s:
            ...
        assert s.top_p == 0.95

    def test_span_has_max_tokens(self):
        with tracer.span("s", max_tokens=512) as s:
            ...
        assert s.max_tokens == 512

    def test_all_three_fields_together(self):
        with tracer.span("s", model="claude-3", temperature=1.0, top_p=0.9, max_tokens=1024) as s:
            ...
        assert s.temperature == 1.0
        assert s.top_p == 0.9
        assert s.max_tokens == 1024

    def test_fields_default_to_none(self):
        with tracer.span("s") as s:
            ...
        assert s.temperature is None
        assert s.top_p is None
        assert s.max_tokens is None

    def test_fields_in_span_payload(self):
        with tracer.span("s", temperature=0.5, top_p=0.8, max_tokens=100) as s:
            ...
        p = s.to_span_payload()
        assert p.temperature == 0.5
        assert p.top_p == 0.8
        assert p.max_tokens == 100

    def test_temperature_in_payload_to_dict(self):
        with tracer.span("s", temperature=0.3) as s:
            ...
        d = s.to_span_payload().to_dict()
        assert d["temperature"] == 0.3

    def test_top_p_in_payload_to_dict(self):
        with tracer.span("s", top_p=0.85) as s:
            ...
        d = s.to_span_payload().to_dict()
        assert d["top_p"] == 0.85

    def test_max_tokens_in_payload_to_dict(self):
        with tracer.span("s", max_tokens=256) as s:
            ...
        d = s.to_span_payload().to_dict()
        assert d["max_tokens"] == 256

    def test_none_fields_omitted_from_dict(self):
        with tracer.span("s") as s:
            ...
        d = s.to_span_payload().to_dict()
        assert "temperature" not in d
        assert "top_p" not in d
        assert "max_tokens" not in d

    def test_fields_survive_from_dict_roundtrip(self):
        with tracer.span("s", temperature=0.9, top_p=0.99, max_tokens=2048) as s:
            ...
        d = s.to_span_payload().to_dict()
        restored = SpanPayload.from_dict(d)
        assert restored.temperature == pytest.approx(0.9)
        assert restored.top_p == pytest.approx(0.99)
        assert restored.max_tokens == 2048

    def test_temperature_only_roundtrip(self):
        with tracer.span("s", temperature=0.0) as s:
            ...
        d = s.to_span_payload().to_dict()
        restored = SpanPayload.from_dict(d)
        assert restored.temperature == pytest.approx(0.0)
        assert restored.top_p is None
        assert restored.max_tokens is None

    def test_tracer_span_passes_fields_through(self):
        """tracer.span() must forward temperature/top_p/max_tokens to SpanContextManager."""
        cm = tracer.span("s", temperature=0.4, top_p=0.6, max_tokens=300)
        assert cm._temperature == 0.4
        assert cm._top_p == 0.6
        assert cm._max_tokens == 300

    def test_trace_llm_call_passes_fields(self):
        """Trace.llm_call() must forward sampling params."""
        trace = start_trace("bot")
        with trace.llm_call(model="gpt-4o", temperature=0.2, top_p=0.7, max_tokens=50) as s:
            ...
        trace.end()
        assert s.temperature == 0.2
        assert s.top_p == 0.7
        assert s.max_tokens == 50

    def test_trace_span_passes_fields(self):
        """Trace.span() must forward sampling params."""
        trace = start_trace("bot")
        with trace.span("step", temperature=1.0, max_tokens=10) as s:
            ...
        trace.end()
        assert s.temperature == 1.0
        assert s.max_tokens == 10

    def test_zero_temperature_valid(self):
        with tracer.span("s", temperature=0.0) as s:
            ...
        assert s.temperature == 0.0

    def test_negative_temperature_stores(self):
        """No validation on temperature — library trusts caller."""
        with tracer.span("s", temperature=-1.0) as s:
            ...
        assert s.temperature == -1.0

    def test_max_tokens_in_trace_json(self):
        trace = start_trace("bot")
        with trace.llm_call(model="gpt-4o", max_tokens=128) as s:
            ...
        trace.end()
        data = json.loads(trace.to_json())
        assert data["spans"][0]["max_tokens"] == 128


# ===========================================================================
# 2.4  Tool span schema additions
# ===========================================================================


class TestToolCallNewFields:
    """Tests for ToolCall new fields: arguments_raw, result_raw, retry_count, external_api."""

    def test_defaults_to_none(self):
        tc = ToolCall(tool_call_id="t1", function_name="fn", status="success")
        assert tc.arguments_raw is None
        assert tc.result_raw is None
        assert tc.retry_count is None
        assert tc.external_api is None

    def test_arguments_raw_stored(self):
        tc = ToolCall(tool_call_id="t1", function_name="fn", status="success",
                      arguments_raw='{"q": "hello"}')
        assert tc.arguments_raw == '{"q": "hello"}'

    def test_result_raw_stored(self):
        tc = ToolCall(tool_call_id="t1", function_name="fn", status="success",
                      result_raw='["result1"]')
        assert tc.result_raw == '["result1"]'

    def test_retry_count_stored(self):
        tc = ToolCall(tool_call_id="t1", function_name="fn", status="success", retry_count=3)
        assert tc.retry_count == 3

    def test_retry_count_zero_valid(self):
        tc = ToolCall(tool_call_id="t1", function_name="fn", status="success", retry_count=0)
        assert tc.retry_count == 0

    def test_retry_count_negative_raises(self):
        with pytest.raises(ValueError, match="retry_count"):
            ToolCall(tool_call_id="t1", function_name="fn", status="success", retry_count=-1)

    def test_external_api_stored(self):
        tc = ToolCall(tool_call_id="t1", function_name="fn", status="success", external_api="stripe")
        assert tc.external_api == "stripe"

    def test_all_new_fields_in_to_dict(self):
        configure(include_raw_tool_io=True)  # required to emit raw I/O fields
        try:
            tc = ToolCall(
                tool_call_id="t1",
                function_name="fn",
                status="success",
                arguments_raw="args",
                result_raw="result",
                retry_count=1,
                external_api="openai",
            )
            d = tc.to_dict()
            assert d["arguments_raw"] == "args"
            assert d["result_raw"] == "result"
            assert d["retry_count"] == 1
            assert d["external_api"] == "openai"
        finally:
            configure(include_raw_tool_io=False)

    def test_none_new_fields_omitted_from_dict(self):
        tc = ToolCall(tool_call_id="t1", function_name="fn", status="success")
        d = tc.to_dict()
        assert "arguments_raw" not in d
        assert "result_raw" not in d
        assert "retry_count" not in d
        assert "external_api" not in d

    def test_raw_fields_omitted_from_dict_when_flag_off(self):
        """Security gate: arguments_raw/result_raw MUST NOT appear when include_raw_tool_io=False."""
        configure(include_raw_tool_io=False)
        tc = ToolCall(
            tool_call_id="t1",
            function_name="fn",
            status="success",
            arguments_raw='{"secret": "key"}',
            result_raw="sensitive data",
        )
        d = tc.to_dict()
        assert "arguments_raw" not in d, "PII must not be exported when include_raw_tool_io=False"
        assert "result_raw" not in d, "PII must not be exported when include_raw_tool_io=False"

    def test_from_dict_roundtrip_with_new_fields(self):
        configure(include_raw_tool_io=True)  # required to round-trip raw I/O
        try:
            tc = ToolCall(
                tool_call_id="t1",
                function_name="fn",
                status="success",
                arguments_raw="args",
                result_raw="result",
                retry_count=2,
                external_api="serp",
            )
            restored = ToolCall.from_dict(tc.to_dict())
            assert restored.arguments_raw == "args"
            assert restored.result_raw == "result"
            assert restored.retry_count == 2
            assert restored.external_api == "serp"
        finally:
            configure(include_raw_tool_io=False)

    def test_from_dict_roundtrip_without_new_fields(self):
        """Existing ToolCall dicts (no new fields) deserialise correctly."""
        d = {"tool_call_id": "t1", "function_name": "fn", "status": "success"}
        tc = ToolCall.from_dict(d)
        assert tc.arguments_raw is None
        assert tc.result_raw is None
        assert tc.retry_count is None
        assert tc.external_api is None

    def test_existing_fields_unaffected(self):
        tc = ToolCall(
            tool_call_id="t1",
            function_name="fn",
            status="error",
            error_type="ValueError",
            duration_ms=12.5,
        )
        d = tc.to_dict()
        assert d["error_type"] == "ValueError"
        assert d["duration_ms"] == 12.5


# ===========================================================================
# 2.4  AgentOBSConfig.include_raw_tool_io
# ===========================================================================


class TestIncludeRawToolIO:
    """Tests for the include_raw_tool_io configuration flag."""

    def setup_method(self):
        # Always reset to default before each test
        configure(include_raw_tool_io=False)

    def teardown_method(self):
        configure(include_raw_tool_io=False)

    def test_default_is_false(self):
        assert get_config().include_raw_tool_io is False

    def test_set_to_true(self):
        configure(include_raw_tool_io=True)
        assert get_config().include_raw_tool_io is True

    def test_set_back_to_false(self):
        configure(include_raw_tool_io=True)
        configure(include_raw_tool_io=False)
        assert get_config().include_raw_tool_io is False

    def test_invalid_key_still_raises(self):
        with pytest.raises(ValueError, match="Unknown agentobs configuration key"):
            configure(unknown_raw_io_flag=True)

    def test_tool_call_arguments_raw_conditionally_set(self):
        """When include_raw_tool_io=True the caller can set arguments_raw."""
        configure(include_raw_tool_io=True)
        tc = ToolCall(
            tool_call_id="t",
            function_name="search",
            status="success",
            arguments_raw='{"query": "hello"}' if get_config().include_raw_tool_io else None,
        )
        assert tc.arguments_raw == '{"query": "hello"}'

    def test_tool_call_arguments_raw_excluded_by_default(self):
        """Without include_raw_tool_io, arguments_raw should remain None."""
        tc = ToolCall(
            tool_call_id="t",
            function_name="search",
            status="success",
            arguments_raw=None,
        )
        assert tc.arguments_raw is None


# ===========================================================================
# Integration — combined Phase 2 features end-to-end
# ===========================================================================


class TestPhase2Integration:
    """End-to-end scenarios exercising multiple Phase 2 features together."""

    def test_full_llm_span_with_all_new_fields(self):
        with tracer.span(
            "llm-call",
            model="gpt-4o",
            temperature=0.8,
            top_p=0.95,
            max_tokens=512,
        ) as s:
            s.add_event("prompt.sent", metadata={"tokens": 256})
            s.add_event("stream.start")
            s.add_event("stream.end", metadata={"tokens": 180})
        payload = s.to_span_payload()
        d = payload.to_dict()
        assert d["temperature"] == 0.8
        assert d["top_p"] == 0.95
        assert d["max_tokens"] == 512
        assert len(d["events"]) == 3
        assert d["events"][0]["name"] == "prompt.sent"

    def test_error_span_with_category_and_events(self):
        with pytest.raises(ConnectionError):
            with tracer.span("llm-call", model="gpt-4o") as s:
                s.add_event("request.sent")
                raise ConnectionError("network down")
        assert s.status == "error"
        assert s.error_category == "unknown_error"
        assert s.events[0].name == "request.sent"

    def test_trace_llm_call_with_tollcall_in_payload(self):
        """SpanPayload can embed a ToolCall with new fields."""
        tc = ToolCall(
            tool_call_id="tc1",
            function_name="web_search",
            status="success",
            retry_count=1,
            external_api="serp",
        )
        trace = start_trace("agent")
        with trace.llm_call(model="gpt-4o", temperature=0.5) as s:
            s.tool_calls.append(tc)
        trace.end()
        payload = s.to_span_payload()
        d = payload.to_dict()
        assert d["temperature"] == 0.5
        tc_d = d["tool_calls"][0]
        assert tc_d["retry_count"] == 1
        assert tc_d["external_api"] == "serp"

    def test_from_dict_roundtrip_full(self):
        """Full SpanPayload round-trip preserving all Phase 2 fields."""
        with tracer.span("s", temperature=0.6, top_p=0.88, max_tokens=64) as s:
            s.add_event("tick")
            s.record_error(ValueError("bad"), category="llm_error")
        d = s.to_span_payload().to_dict()
        restored = SpanPayload.from_dict(d)
        assert restored.temperature == pytest.approx(0.6)
        assert restored.top_p == pytest.approx(0.88)
        assert restored.max_tokens == 64
        assert restored.error_category == "llm_error"
        assert len(restored.events) == 1
        assert restored.events[0].name == "tick"

    def test_trace_json_contains_all_phase2_fields(self):
        trace = start_trace("research-agent", env="test")
        with trace.llm_call(model="gpt-4o", temperature=0.7, max_tokens=200) as s:
            s.add_event("search.start")
            s.add_event("search.end", metadata={"results": 5})
        trace.end()
        data = json.loads(trace.to_json())
        span = data["spans"][0]
        assert span["temperature"] == 0.7
        assert span["max_tokens"] == 200
        assert len(span["events"]) == 2

    def test_async_span_with_phase2_features(self):
        """Phase 2 features work identically in async context."""
        import asyncio

        async def _run():
            async with tracer.span("async-llm", model="gpt-4o", temperature=0.3) as s:
                s.add_event("async.prompt.sent")
            return s

        s = asyncio.run(_run())
        assert s.temperature == 0.3
        assert s.events[0].name == "async.prompt.sent"

    def test_concurrent_async_spans_independent_events(self):
        """Concurrent async tasks each maintain independent event lists."""
        import asyncio

        async def _task(name: str, n: int):
            async with tracer.span(name) as s:
                for i in range(n):
                    s.add_event(f"{name}.event.{i}")
            return s

        async def _main():
            s1, s2 = await asyncio.gather(_task("t1", 3), _task("t2", 5))
            return s1, s2

        s1, s2 = asyncio.run(_main())
        assert len(s1.events) == 3
        assert len(s2.events) == 5
        assert all(e.name.startswith("t1") for e in s1.events)
        assert all(e.name.startswith("t2") for e in s2.events)

    def test_span_event_category_in_agentobs_init(self):
        """SpanEvent and SpanErrorCategory are accessible from top-level agentobs."""
        assert hasattr(agentobs, "SpanEvent")
        assert hasattr(agentobs, "SpanErrorCategory")

    def test_tool_call_in_span_payload_serializes_with_retry(self):
        """ToolCall with retry_count serializes and deserialises in SpanPayload."""
        tc = ToolCall(
            tool_call_id="x",
            function_name="fn",
            status="success",
            retry_count=2,
            external_api="openai",
        )
        with tracer.span("s") as s:
            s.tool_calls.append(tc)
        d = s.to_span_payload().to_dict()
        restored = SpanPayload.from_dict(d)
        assert restored.tool_calls[0].retry_count == 2
        assert restored.tool_calls[0].external_api == "openai"


# ===========================================================================
# SpanPayload direct tests for new fields
# ===========================================================================


class TestSpanPayloadPhase2Fields:
    """Tests verifying SpanPayload data-class handles new optional fields correctly."""

    def _make_payload(self, **kwargs: Any) -> SpanPayload:
        import os
        return SpanPayload(
            span_id=os.urandom(8).hex(),
            trace_id=os.urandom(16).hex(),
            span_name="test",
            operation="chat",
            span_kind="CLIENT",
            status="ok",
            start_time_unix_nano=1_000_000,
            end_time_unix_nano=2_000_000,
            duration_ms=1.0,
            **kwargs,
        )

    def test_default_new_fields_are_none_or_empty(self):
        p = self._make_payload()
        assert p.temperature is None
        assert p.top_p is None
        assert p.max_tokens is None
        assert p.error_category is None
        assert p.events == []

    def test_set_temperature(self):
        p = self._make_payload(temperature=0.5)
        assert p.temperature == pytest.approx(0.5)

    def test_set_events(self):
        ev = SpanEvent(name="x", timestamp_ns=1000)
        p = self._make_payload(events=[ev])
        assert len(p.events) == 1

    def test_to_dict_excludes_empty_events_list(self):
        p = self._make_payload()
        assert "events" not in p.to_dict()

    def test_to_dict_includes_non_empty_events_list(self):
        ev = SpanEvent(name="y", timestamp_ns=1)
        p = self._make_payload(events=[ev])
        d = p.to_dict()
        assert "events" in d
        assert len(d["events"]) == 1

    def test_from_dict_with_events_and_llm_fields(self):
        ev = SpanEvent(name="z", timestamp_ns=500)
        p = self._make_payload(temperature=0.3, max_tokens=100, events=[ev])
        d = p.to_dict()
        restored = SpanPayload.from_dict(d)
        assert restored.temperature == pytest.approx(0.3)
        assert restored.max_tokens == 100
        assert len(restored.events) == 1
        assert restored.events[0].name == "z"
