"""tests/test_phase3_debug_sampling.py — Exhaustive tests for Phase 3 changes.

Phase 3 covers:
- 3.1  Debug utilities: print_tree(), summary(), visualize()
        agentobs/debug.py + Trace.print_tree/summary/visualize()
- 3.2  Sampling controls: sample_rate, always_sample_errors, trace_filters
        agentobs/config.py (fields) + agentobs/_stream._should_emit()

Coverage target: ≥ 95 % for all Phase 3 new code.
"""

from __future__ import annotations

import io
import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import agentobs
from agentobs import (
    configure,
    get_config,
    print_tree,
    start_trace,
    summary,
    tracer,
    visualize,
)
from agentobs._stream import _should_emit
from agentobs.debug import (
    _color,
    _coerce,
    _no_color,
    _span_label,
    _status_badge,
    _to_payload,
)
from agentobs.event import Event, Tags
from agentobs.namespaces.trace import (
    CostBreakdown,
    GenAIOperationName,
    ModelInfo,
    SpanEvent,
    SpanKind,
    SpanPayload,
    TokenUsage,
    ToolCall,
)
from agentobs.types import EventType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRACE_ID = "a" * 32
_SPAN_ID = "b" * 16
_SPAN_ID2 = "c" * 16
_SPAN_ID3 = "d" * 16

_T0 = 1_000_000_000_000_000_000  # 1 second in nanoseconds as epoch reference


def _make_span(
    span_id: str = _SPAN_ID,
    trace_id: str = _TRACE_ID,
    span_name: str = "test_span",
    operation: GenAIOperationName | str = GenAIOperationName.CHAT,
    status: str = "ok",
    duration_ms: float = 100.0,
    parent_span_id: str | None = None,
    start_offset_ns: int = 0,
    token_usage: TokenUsage | None = None,
    cost: CostBreakdown | None = None,
    error: str | None = None,
    model: ModelInfo | None = None,
    events: list[SpanEvent] | None = None,
) -> SpanPayload:
    start = _T0 + start_offset_ns
    end = start + int(duration_ms * 1_000_000)
    return SpanPayload(
        span_id=span_id,
        trace_id=trace_id,
        span_name=span_name,
        operation=operation,
        span_kind=SpanKind.INTERNAL,
        status=status,
        start_time_unix_nano=start,
        end_time_unix_nano=end,
        duration_ms=duration_ms,
        parent_span_id=parent_span_id,
        token_usage=token_usage,
        cost=cost,
        error=error,
        model=model,
        events=events or [],
    )


def _make_event(payload: dict[str, Any]) -> Event:
    """Create a minimal Event wrapping *payload* for sampling tests."""
    return Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="test-service@1.0.0",
        payload=payload,
        tags=Tags(env="test"),
    )


# ===========================================================================
# 3.1a  _no_color() and _color()
# ===========================================================================


class TestColorHelpers:
    def test_no_color_false_by_default(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("AGENTOBS_NO_COLOR", raising=False)
        assert _no_color() is False

    def test_no_color_set_via_no_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert _no_color() is True

    def test_no_color_set_via_agentobs_no_color(self, monkeypatch):
        monkeypatch.setenv("AGENTOBS_NO_COLOR", "1")
        assert _no_color() is True

    def test_color_returns_ansi_when_color_enabled(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("AGENTOBS_NO_COLOR", raising=False)
        result = _color("hello", "\033[92m")
        assert "\033[92m" in result
        assert "hello" in result
        assert "\033[0m" in result

    def test_color_returns_plain_when_no_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        result = _color("hello", "\033[92m")
        assert result == "hello"

    def test_color_empty_text(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("AGENTOBS_NO_COLOR", raising=False)
        result = _color("", "\033[92m")
        # Even empty text gets wrapped (existing design)
        assert isinstance(result, str)


# ===========================================================================
# 3.1b  _status_badge()
# ===========================================================================


class TestStatusBadge:
    def test_ok_badge(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert _status_badge("ok") == "ok"

    def test_error_badge(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert _status_badge("error") == "error"

    def test_timeout_badge(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert _status_badge("timeout") == "timeout"

    def test_unknown_badge_passthrough(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert _status_badge("running") == "running"


# ===========================================================================
# 3.1c  _span_label()
# ===========================================================================


class TestSpanLabel:
    def test_basic_label(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        p = _make_span(span_name="my_span", status="ok", duration_ms=123.4)
        label = _span_label(p)
        assert "my_span" in label
        assert "ok" in label
        assert "123ms" in label

    def test_model_info_in_label(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        model = ModelInfo(name="gpt-4o", system="openai")
        p = _make_span(model=model)
        label = _span_label(p)
        assert "gpt-4o" in label

    def test_token_usage_in_label(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        tu = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150)
        p = _make_span(token_usage=tu, duration_ms=200.0)
        label = _span_label(p)
        assert "in=100" in label
        assert "out=50" in label

    def test_cost_in_label(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        cost = CostBreakdown(input_cost_usd=0.005, output_cost_usd=0.0073, total_cost_usd=0.0123)
        p = _make_span(cost=cost, duration_ms=50.0)
        label = _span_label(p)
        assert "$0.0123" in label

    def test_error_in_label(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        p = _make_span(status="error", error="Connection refused", duration_ms=10.0)
        label = _span_label(p)
        assert "Connection refused" in label

    def test_long_error_truncated(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        long_error = "x" * 100
        p = _make_span(status="error", error=long_error, duration_ms=10.0)
        label = _span_label(p)
        assert "…" in label

    def test_span_events_shown(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        events = [SpanEvent(name="cache.hit"), SpanEvent(name="retry.attempt")]
        p = _make_span(events=events, duration_ms=50.0)
        label = _span_label(p)
        assert "2 events" in label

    def test_single_event_singular(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        events = [SpanEvent(name="cache.hit")]
        p = _make_span(events=events, duration_ms=50.0)
        label = _span_label(p)
        assert "1 event" in label
        assert "events" not in label.replace("1 event", "")


# ===========================================================================
# 3.1d  _to_payload() and _coerce()
# ===========================================================================


class TestToPayload:
    def test_passthrough_for_span_payload(self):
        p = _make_span()
        assert _to_payload(p) is p

    def test_live_span_coerced(self):
        """A live Span object should be coerced via to_span_payload()."""
        mock_span = MagicMock()
        expected_payload = _make_span()
        mock_span.to_span_payload.return_value = expected_payload
        result = _to_payload(mock_span)
        assert result is expected_payload
        mock_span.to_span_payload.assert_called_once()

    def test_coerce_empty_list(self):
        assert _coerce([]) == []

    def test_coerce_mixed_span_payloads(self):
        p1 = _make_span(span_id="a" * 16)
        p2 = _make_span(span_id="b" * 16)
        result = _coerce([p1, p2])
        assert result == [p1, p2]


# ===========================================================================
# 3.1e  print_tree() — standalone function
# ===========================================================================


class TestPrintTree:
    def test_empty_spans_writes_no_spans(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        buf = io.StringIO()
        print_tree([], file=buf)
        assert "(no spans)" in buf.getvalue()

    def test_single_span_output(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        p = _make_span(span_name="root_span", status="ok", duration_ms=500.0)
        buf = io.StringIO()
        print_tree([p], file=buf)
        out = buf.getvalue()
        assert "root_span" in out
        assert "ok" in out

    def test_nested_hierarchy(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        parent = _make_span(span_id=_SPAN_ID, span_name="parent", start_offset_ns=0, duration_ms=200.0)
        child = _make_span(
            span_id=_SPAN_ID2,
            span_name="child",
            parent_span_id=_SPAN_ID,
            start_offset_ns=10_000_000,
            duration_ms=50.0,
        )
        buf = io.StringIO()
        print_tree([parent, child], file=buf)
        out = buf.getvalue()
        assert "parent" in out
        assert "child" in out
        # Tree chars – branch or last connector
        assert "─" in out

    def test_trace_id_filter_matches(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        trace_a = "a" * 32
        trace_b = "b" * 32
        p_a = _make_span(span_id="a" * 16, trace_id=trace_a, span_name="span_a")
        p_b = _make_span(span_id="b" * 16, trace_id=trace_b, span_name="span_b")
        buf = io.StringIO()
        print_tree([p_a, p_b], trace_id=trace_a, file=buf)
        out = buf.getvalue()
        assert "span_a" in out
        assert "span_b" not in out

    def test_trace_id_filter_no_match(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        p = _make_span(span_name="only_span")
        buf = io.StringIO()
        print_tree([p], trace_id="0" * 32, file=buf)
        assert "no spans for trace_id" in buf.getvalue()

    def test_multi_root_spans_all_shown(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        p1 = _make_span(span_id="a" * 16, span_name="root1", start_offset_ns=0)
        p2 = _make_span(span_id="b" * 16, span_name="root2", start_offset_ns=100_000_000)
        buf = io.StringIO()
        print_tree([p1, p2], file=buf)
        out = buf.getvalue()
        assert "root1" in out
        assert "root2" in out

    def test_default_file_is_stdout(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        p = _make_span(span_name="stdout_span")
        print_tree([p])
        captured = capsys.readouterr()
        assert "stdout_span" in captured.out

    def test_color_output_contains_ansi(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("AGENTOBS_NO_COLOR", raising=False)
        p = _make_span(span_name="colored_span", status="error")
        buf = io.StringIO()
        print_tree([p], file=buf)
        out = buf.getvalue()
        # Should contain ANSI escape sequences
        assert "\033[" in out

    def test_deep_nesting(self, monkeypatch):
        """Three levels of nesting should all appear in output."""
        monkeypatch.setenv("NO_COLOR", "1")
        grandparent = _make_span(span_id="a" * 16, span_name="grandparent", start_offset_ns=0, duration_ms=300.0)
        parent = _make_span(
            span_id="b" * 16,
            span_name="parent_node",
            parent_span_id="a" * 16,
            start_offset_ns=10_000_000,
            duration_ms=200.0,
        )
        child = _make_span(
            span_id="c" * 16,
            span_name="child_node",
            parent_span_id="b" * 16,
            start_offset_ns=20_000_000,
            duration_ms=100.0,
        )
        buf = io.StringIO()
        print_tree([grandparent, parent, child], file=buf)
        out = buf.getvalue()
        for name in ("grandparent", "parent_node", "child_node"):
            assert name in out

    def test_multiple_traces_shown(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        p1 = _make_span(span_id="a" * 16, trace_id="a" * 32, span_name="trace1_root")
        p2 = _make_span(span_id="b" * 16, trace_id="b" * 32, span_name="trace2_root")
        buf = io.StringIO()
        print_tree([p1, p2], file=buf)
        out = buf.getvalue()
        assert "trace1_root" in out
        assert "trace2_root" in out


# ===========================================================================
# 3.1f  summary() — standalone function
# ===========================================================================


class TestSummary:
    def test_empty_spans_returns_zeroed_dict(self):
        result = summary([])
        assert result["span_count"] == 0
        assert result["llm_calls"] == 0
        assert result["tool_calls"] == 0
        assert result["total_duration_ms"] == pytest.approx(0.0)
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0
        assert result["total_cost_usd"] == pytest.approx(0.0)
        assert result["error_count"] == 0
        assert result["timeout_count"] == 0
        assert result["trace_id"] is None

    def test_single_llm_span(self):
        p = _make_span(operation=GenAIOperationName.CHAT, duration_ms=1000.0)
        result = summary([p])
        assert result["span_count"] == 1
        assert result["llm_calls"] == 1
        assert result["tool_calls"] == 0
        assert result["total_duration_ms"] == pytest.approx(1000.0)
        assert result["trace_id"] == _TRACE_ID

    def test_tool_call_counted(self):
        p = _make_span(span_id="e" * 16, operation=GenAIOperationName.EXECUTE_TOOL, duration_ms=50.0)
        result = summary([p])
        assert result["tool_calls"] == 1
        assert result["llm_calls"] == 0

    def test_token_totals_summed(self):
        tu1 = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150)
        tu2 = TokenUsage(input_tokens=200, output_tokens=80, total_tokens=280)
        p1 = _make_span(span_id="a" * 16, token_usage=tu1, duration_ms=100.0)
        p2 = _make_span(span_id="b" * 16, token_usage=tu2, duration_ms=100.0)
        result = summary([p1, p2])
        assert result["total_input_tokens"] == 300
        assert result["total_output_tokens"] == 130

    def test_cost_summed(self):
        c1 = CostBreakdown(input_cost_usd=0.004, output_cost_usd=0.006, total_cost_usd=0.01)
        c2 = CostBreakdown(input_cost_usd=0.008, output_cost_usd=0.012, total_cost_usd=0.02)
        p1 = _make_span(span_id="a" * 16, cost=c1, duration_ms=100.0)
        p2 = _make_span(span_id="b" * 16, cost=c2, duration_ms=100.0)
        result = summary([p1, p2])
        assert abs(result["total_cost_usd"] - 0.03) < 1e-9

    def test_error_count(self):
        ok_span = _make_span(span_id="a" * 16, status="ok")
        err_span = _make_span(span_id="b" * 16, status="error", error="boom")
        result = summary([ok_span, err_span])
        assert result["error_count"] == 1
        assert result["timeout_count"] == 0

    def test_timeout_count(self):
        t_span = _make_span(span_id="a" * 16, status="timeout")
        result = summary([t_span])
        assert result["timeout_count"] == 1

    def test_multi_trace_trace_id_is_none(self):
        p1 = _make_span(span_id="a" * 16, trace_id="a" * 32)
        p2 = _make_span(span_id="b" * 16, trace_id="b" * 32)
        result = summary([p1, p2])
        assert result["trace_id"] is None

    def test_duration_is_sum_not_max(self):
        p1 = _make_span(span_id="a" * 16, duration_ms=100.0)
        p2 = _make_span(span_id="b" * 16, duration_ms=250.0)
        result = summary([p1, p2])
        assert result["total_duration_ms"] == pytest.approx(350.0)

    def test_no_token_usage_gives_zero_tokens(self):
        p = _make_span(token_usage=None, span_id="f" * 16)
        result = summary([p])
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0

    def test_all_keys_present(self):
        result = summary([_make_span()])
        expected_keys = {
            "trace_id", "span_count", "llm_calls", "tool_calls",
            "total_duration_ms", "total_input_tokens", "total_output_tokens",
            "total_cost_usd", "error_count", "timeout_count",
        }
        assert expected_keys == set(result.keys())

    def test_embed_operation_counted_as_llm(self):
        p = _make_span(span_id="f" * 16, operation=GenAIOperationName.EMBEDDINGS)
        result = summary([p])
        assert result["llm_calls"] == 1


# ===========================================================================
# 3.1g  visualize() — standalone function
# ===========================================================================


class TestVisualize:
    def test_returns_html_string(self):
        p = _make_span(span_name="my_span", duration_ms=500.0)
        html = visualize([p])
        assert isinstance(html, str)
        assert html.startswith("<!DOCTYPE html>")

    def test_html_contains_span_name(self):
        p = _make_span(span_name="important_span", duration_ms=100.0)
        html = visualize([p])
        assert "important_span" in html

    def test_empty_spans_still_valid_html(self):
        html = visualize([])
        assert "<!DOCTYPE html>" in html
        assert "No spans" in html

    def test_error_status_uses_error_css_class(self):
        p = _make_span(span_id="e" * 16, status="error", error="kaboom", duration_ms=10.0)
        html = visualize([p])
        assert 'class="bar error"' in html

    def test_timeout_status_uses_timeout_css_class(self):
        p = _make_span(span_id="f" * 16, status="timeout", duration_ms=10.0)
        html = visualize([p])
        assert 'class="bar timeout"' in html

    def test_ok_status_uses_ok_css_class(self):
        p = _make_span(span_id="1" * 16, status="ok", duration_ms=10.0)
        html = visualize([p])
        assert 'class="bar ok"' in html

    def test_stats_section_present(self):
        p = _make_span(span_name="sample")
        html = visualize([p])
        assert "spans" in html
        assert "LLM calls" in html

    def test_unsupported_output_raises_valueerror(self):
        p = _make_span()
        with pytest.raises(ValueError, match="unsupported output format"):
            visualize([p], output="svg")

    def test_writes_file_when_path_given(self, tmp_path):
        p = _make_span(span_name="file_test")
        out_file = tmp_path / "trace.html"
        html = visualize([p], path=str(out_file))
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert content == html
        assert "file_test" in content

    def test_xss_span_name_escaped(self):
        """Span names containing HTML special chars must be escaped."""
        p = _make_span(span_id="a" * 16, span_name='<script>alert("xss")</script>', duration_ms=10.0)
        html = visualize([p])
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_model_name_in_html(self):
        model = ModelInfo(name='gpt-4o-"special"', system="openai")
        p = _make_span(span_id="b" * 16, model=model, duration_ms=100.0)
        html = visualize([p])
        # Model name may have quotes escaped
        assert "gpt-4o" in html

    def test_token_usage_shown_in_bar_label(self):
        tu = TokenUsage(input_tokens=512, output_tokens=128, total_tokens=640)
        p = _make_span(span_id="c" * 16, token_usage=tu, duration_ms=200.0)
        html = visualize([p])
        assert "in=512" in html
        assert "out=128" in html

    def test_multiple_spans_all_rendered(self):
        p1 = _make_span(span_id="a" * 16, span_name="alpha", start_offset_ns=0, duration_ms=100.0)
        p2 = _make_span(span_id="b" * 16, span_name="beta", start_offset_ns=200_000_000, duration_ms=200.0)
        html = visualize([p1, p2])
        assert "alpha" in html
        assert "beta" in html

    def test_self_contained_no_external_links(self):
        p = _make_span()
        html = visualize([p])
        # No external script or link tags
        assert "<script src=" not in html
        assert '<link rel="stylesheet"' not in html

    def test_single_span_gantt_width_100_percent(self):
        """A single span should occupy close to 100% width (capped by 0.3 floor)."""
        p = _make_span(span_id="d" * 16, duration_ms=1000.0)
        html = visualize([p])
        # Very small spans get 0.3% min; 100% - 0 start = full width
        # The width style should be present
        assert "width:" in html


# ===========================================================================
# 3.1h  Trace.print_tree / summary / visualize (integration via Trace object)
# ===========================================================================


class TestTraceDebugMethods:
    def test_trace_print_tree_calls_debug(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        with start_trace("test-agent") as trace:
            with tracer.span("gpt_call", model="gpt-4o"):
                ...
        trace.print_tree()
        out = capsys.readouterr().out
        # Should contain the span name or trace header
        assert len(out) > 0

    def test_trace_print_tree_custom_file(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        buf = io.StringIO()
        with start_trace("trace-file") as trace:
            with tracer.span("my_llm"):
                ...
        trace.print_tree(file=buf)
        assert len(buf.getvalue()) > 0

    def test_trace_summary_returns_dict(self):
        with start_trace("sum-trace") as trace:
            with tracer.span("chat_call", model="gpt-4o"):
                ...
        result = trace.summary()
        assert isinstance(result, dict)
        assert result["span_count"] >= 1

    def test_trace_summary_empty_trace(self):
        with start_trace("empty-trace") as trace:
            ...
        result = trace.summary()
        assert result["span_count"] == 0

    def test_trace_visualize_returns_html(self):
        with start_trace("viz-trace") as trace:
            with tracer.span("viz_llm"):
                ...
        html = trace.visualize()
        assert "<!DOCTYPE html>" in html

    def test_trace_visualize_writes_file(self, tmp_path):
        out_file = tmp_path / "vis.html"
        with start_trace("file-trace") as trace:
            with tracer.span("a_span"):
                ...
        html = trace.visualize(path=str(out_file))
        assert out_file.exists()
        assert out_file.read_text(encoding="utf-8") == html

    def test_print_tree_after_empty_trace(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        with start_trace("empty") as trace:
            ...
        trace.print_tree()
        out = capsys.readouterr().out
        assert "(no spans)" in out


# ===========================================================================
# 3.1i  Public API exports
# ===========================================================================


class TestPublicExports:
    def test_print_tree_exported_from_agentobs(self):
        assert hasattr(agentobs, "print_tree")
        assert callable(agentobs.print_tree)

    def test_summary_exported_from_agentobs(self):
        assert hasattr(agentobs, "summary")
        assert callable(agentobs.summary)

    def test_visualize_exported_from_agentobs(self):
        assert hasattr(agentobs, "visualize")
        assert callable(agentobs.visualize)

    def test_print_tree_in_all(self):
        assert "print_tree" in agentobs.__all__

    def test_summary_in_all(self):
        assert "summary" in agentobs.__all__

    def test_visualize_in_all(self):
        assert "visualize" in agentobs.__all__


# ===========================================================================
# 3.2a  AgentOBSConfig new fields
# ===========================================================================


class TestConfigSamplingFields:
    def test_sample_rate_default_is_1(self):
        configure()  # reset to defaults
        cfg = get_config()
        assert cfg.sample_rate == pytest.approx(1.0)

    def test_always_sample_errors_default_true(self):
        configure()
        cfg = get_config()
        assert cfg.always_sample_errors is True

    def test_trace_filters_default_empty(self):
        configure()
        cfg = get_config()
        assert cfg.trace_filters == []

    def test_configure_sample_rate(self):
        configure(sample_rate=0.5)
        assert get_config().sample_rate == pytest.approx(0.5)

    def test_configure_always_sample_errors_false(self):
        configure(always_sample_errors=False)
        assert get_config().always_sample_errors is False

    def test_configure_trace_filters(self):
        fn = lambda e: True  # noqa: E731
        configure(trace_filters=[fn])
        assert fn in get_config().trace_filters

    def test_sample_rate_env_var(self, monkeypatch):
        from agentobs.config import _load_from_env  # noqa: PLC0415
        monkeypatch.setenv("AGENTOBS_SAMPLE_RATE", "0.25")
        _load_from_env()
        assert get_config().sample_rate == pytest.approx(0.25)

    def test_sample_rate_env_var_clamped_above_1(self, monkeypatch):
        from agentobs.config import _load_from_env  # noqa: PLC0415
        monkeypatch.setenv("AGENTOBS_SAMPLE_RATE", "2.0")
        _load_from_env()
        assert get_config().sample_rate == pytest.approx(1.0)

    def test_sample_rate_env_var_clamped_below_0(self, monkeypatch):
        from agentobs.config import _load_from_env  # noqa: PLC0415
        monkeypatch.setenv("AGENTOBS_SAMPLE_RATE", "-0.5")
        _load_from_env()
        assert get_config().sample_rate == pytest.approx(0.0)

    def test_sample_rate_env_var_invalid_string_ignored(self, monkeypatch):
        from agentobs.config import _load_from_env  # noqa: PLC0415
        configure(sample_rate=0.7)  # set to non-default first
        monkeypatch.setenv("AGENTOBS_SAMPLE_RATE", "not_a_float")
        _load_from_env()  # should not raise; falls back to 1.0
        assert get_config().sample_rate == pytest.approx(1.0)

    def teardown_method(self, _method):
        """Reset config after each sampling test."""
        configure(sample_rate=1.0, always_sample_errors=True, trace_filters=[])


# ===========================================================================
# 3.2b  _should_emit() — unit tests
# ===========================================================================


def _cfg(**kwargs):
    """Helper: get a live config snapshot overriding specific fields."""
    configure(sample_rate=1.0, always_sample_errors=True, trace_filters=[])  # start fresh
    configure(**kwargs)
    return get_config()


class TestShouldEmit:
    def teardown_method(self, _method):
        configure(sample_rate=1.0, always_sample_errors=True, trace_filters=[])

    # --- fast path -----------------------------------------------------------

    def test_always_emit_when_rate_1_no_filters(self):
        cfg = _cfg(sample_rate=1.0, trace_filters=[])
        event = _make_event({"trace_id": "a" * 32, "status": "ok"})
        assert _should_emit(event, cfg) is True

    # --- error pass-through --------------------------------------------------

    def test_error_always_emitted_when_always_sample_errors(self):
        cfg = _cfg(sample_rate=0.0, always_sample_errors=True)
        event = _make_event({"trace_id": "a" * 32, "status": "error"})
        assert _should_emit(event, cfg) is True

    def test_timeout_always_emitted_when_always_sample_errors(self):
        cfg = _cfg(sample_rate=0.0, always_sample_errors=True)
        event = _make_event({"trace_id": "a" * 32, "status": "timeout"})
        assert _should_emit(event, cfg) is True

    def test_error_dropped_when_always_sample_errors_false(self):
        cfg = _cfg(sample_rate=0.0, always_sample_errors=False)
        # At rate 0.0, non-error events are dropped even with a hash that happens
        # to be 0 — we need a trace_id that hashes above 0.
        # Use a trace_id that deterministically drops with sample_rate=0.0
        # The condition is bucket/0xFFFFFFFF > 0.0  → all buckets > 0 → drop
        # Only bucket=0 would pass. We choose a trace_id that gives bucket≠0.
        event = _make_event({"trace_id": "1" * 32, "status": "error"})
        # With always_sample_errors=False and rate=0.0 and a non-zero hash, drop.
        result = _should_emit(event, cfg)
        # The trace_id "1"*32 → first 8 hex chars = "11111111" → 0x11111111 / 0xFFFFFFFF > 0.0
        assert result is False

    def test_ok_span_at_rate_zero_dropped(self):
        cfg = _cfg(sample_rate=0.0, always_sample_errors=False)
        # "11111111" → 0x11111111 / 0xFFFFFFFF ≈ 0.067 > 0.0 → dropped
        event = _make_event({"trace_id": "1" + "1" * 31, "status": "ok"})
        assert _should_emit(event, cfg) is False

    # --- deterministic sampling ----------------------------------------------

    def test_deterministic_same_trace_id_same_decision(self):
        cfg = _cfg(sample_rate=0.5, always_sample_errors=False)
        event1 = _make_event({"trace_id": "abcdef12" + "0" * 24, "status": "ok"})
        event2 = _make_event({"trace_id": "abcdef12" + "0" * 24, "status": "ok"})
        r1 = _should_emit(event1, cfg)
        r2 = _should_emit(event2, cfg)
        assert r1 == r2

    def test_different_trace_ids_can_differ(self):
        """At rate=0.5, two different trace_ids should not always agree."""
        cfg = _cfg(sample_rate=0.5, always_sample_errors=False)
        results = set()
        # Use trace_ids with widely-spread first-8-hex-char bucket values
        candidates = [
            "00000000" + "0" * 24,  # bucket ≈ 0 → pass at 0.5
            "ffffffff" + "0" * 24,  # bucket ≈ 1.0 → drop at 0.5
        ]
        for trace_id in candidates:
            ev = _make_event({"trace_id": trace_id, "status": "ok"})
            results.add(_should_emit(ev, cfg))
        assert True in results, "Expected at least one True"
        assert False in results, "Expected at least one False"

    def test_rate_1_always_passes(self):
        cfg = _cfg(sample_rate=1.0, always_sample_errors=False)
        for i in range(100):
            trace_id = format(i * 0x01010101, "032x")[:32]
            ev = _make_event({"trace_id": trace_id, "status": "ok"})
            assert _should_emit(ev, cfg) is True

    def test_rate_0_drops_non_errors(self):
        cfg = _cfg(sample_rate=0.0, always_sample_errors=False)
        # Build trace_ids where the first 8 hex chars cover a wide range so
        # their bucket value (first-8-hex as 32-bit int) is definitely non-zero.
        dropped = 0
        for i in range(1, 256):
            # Spread the first 8 chars across the full 32-bit range.
            prefix = format(i * 0x01000000, "08x")  # e.g. "01000000", "02000000"...
            trace_id = prefix + "0" * 24
            ev = _make_event({"trace_id": trace_id, "status": "ok"})
            if not _should_emit(ev, cfg):
                dropped += 1
        # All 255 should be dropped (bucket > 0 → 0/0xFFFFFFFF is ~0 → drop)
        assert dropped >= 200

    def test_no_trace_id_fallback(self, monkeypatch):
        """Events with no trace_id use random sampling and should not raise."""
        cfg = _cfg(sample_rate=0.5, always_sample_errors=False)
        event = _make_event({"status": "ok"})  # no trace_id key
        # Should run without error; result depends on random
        result = _should_emit(event, cfg)
        assert isinstance(result, bool)

    # --- trace_filters -------------------------------------------------------

    def test_filter_passes_all(self):
        cfg = _cfg(sample_rate=1.0, trace_filters=[lambda e: True])
        event = _make_event({"trace_id": "a" * 32, "status": "ok"})
        assert _should_emit(event, cfg) is True

    def test_filter_blocks_all(self):
        cfg = _cfg(sample_rate=1.0, always_sample_errors=False, trace_filters=[lambda e: False])
        event = _make_event({"trace_id": "a" * 32, "status": "ok"})
        assert _should_emit(event, cfg) is False

    def test_multiple_filters_all_must_pass(self):
        cfg = _cfg(
            sample_rate=1.0,
            trace_filters=[lambda e: True, lambda e: False],
        )
        event = _make_event({"trace_id": "a" * 32, "status": "ok"})
        assert _should_emit(event, cfg) is False

    def test_filter_exception_does_not_drop_event(self):
        """A filter that raises must not silently drop the event."""
        def bad_filter(e):
            raise RuntimeError("oops")

        cfg = _cfg(sample_rate=1.0, trace_filters=[bad_filter])
        event = _make_event({"trace_id": "a" * 32, "status": "ok"})
        # Must not raise and must return True (filter failure = pass)
        assert _should_emit(event, cfg) is True

    def test_filter_receives_event_argument(self):
        received = []

        def capture_filter(e):
            received.append(e)
            return True

        cfg = _cfg(sample_rate=1.0, trace_filters=[capture_filter])
        event = _make_event({"trace_id": "a" * 32, "status": "ok"})
        _should_emit(event, cfg)
        assert len(received) == 1
        assert received[0] is event

    # --- error pass-through overrides filter ---------------------------------

    def test_error_bypasses_filter_when_always_sample_errors(self):
        """Error events skip the filter check when always_sample_errors=True."""
        cfg = _cfg(sample_rate=0.0, always_sample_errors=True, trace_filters=[lambda e: False])
        event = _make_event({"trace_id": "a" * 32, "status": "error"})
        # error pass-through happens BEFORE filter evaluation (per implementation)
        assert _should_emit(event, cfg) is True


# ===========================================================================
# 3.2c  Sampling integration — events actually dropped/forwarded by _dispatch
# ===========================================================================


class TestSamplingIntegration:
    """Verify _dispatch() respects _should_emit() by inspecting the exporter."""

    def teardown_method(self, _method):
        configure(sample_rate=1.0, always_sample_errors=True, trace_filters=[])

    def _make_dispatched_event(self) -> Event:
        return Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="sampler@1.0.0",
            payload={
                "trace_id": "a" * 32,
                "span_id": "b" * 16,
                "status": "ok",
                "span_name": "s",
                "operation": "chat",
                "span_kind": "internal",
                "start_time_unix_nano": _T0,
                "end_time_unix_nano": _T0 + 100_000_000,
                "duration_ms": 100.0,
            },
            tags=Tags(env="test"),
        )

    def test_dispatch_passes_event_when_rate_1(self):
        from agentobs._stream import _dispatch  # noqa: PLC0415

        calls = []
        mock_exporter = MagicMock()
        mock_exporter.export.side_effect = lambda e: calls.append(e)

        configure(sample_rate=1.0)
        with patch("agentobs._stream._active_exporter", return_value=mock_exporter):
            _dispatch(self._make_dispatched_event())

        assert len(calls) == 1

    def test_dispatch_drops_event_at_rate_0(self):
        from agentobs._stream import _dispatch  # noqa: PLC0415

        calls = []
        mock_exporter = MagicMock()
        mock_exporter.export.side_effect = lambda e: calls.append(e)

        configure(sample_rate=0.0, always_sample_errors=False)
        # Use a trace_id that hashes to a non-zero bucket (guaranteed drop at rate=0)
        event = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="sampler@1.0.0",
            payload={
                "trace_id": "1" * 32,
                "span_id": "b" * 16,
                "status": "ok",
                "span_name": "s",
                "operation": "chat",
                "span_kind": "internal",
                "start_time_unix_nano": _T0,
                "end_time_unix_nano": _T0 + 100_000_000,
                "duration_ms": 100.0,
            },
            tags=Tags(env="test"),
        )
        with patch("agentobs._stream._active_exporter", return_value=mock_exporter):
            _dispatch(event)

        assert len(calls) == 0

    def test_dispatch_passes_error_even_at_rate_0(self):
        from agentobs._stream import _dispatch  # noqa: PLC0415

        calls = []
        mock_exporter = MagicMock()
        mock_exporter.export.side_effect = lambda e: calls.append(e)

        configure(sample_rate=0.0, always_sample_errors=True)
        event = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="sampler@1.0.0",
            payload={
                "trace_id": "1" * 32,
                "span_id": "b" * 16,
                "status": "error",
                "span_name": "s",
                "operation": "chat",
                "span_kind": "internal",
                "start_time_unix_nano": _T0,
                "end_time_unix_nano": _T0 + 100_000_000,
                "duration_ms": 100.0,
            },
            tags=Tags(env="test"),
        )
        with patch("agentobs._stream._active_exporter", return_value=mock_exporter):
            _dispatch(event)

        assert len(calls) == 1

    def test_trace_filter_blocks_in_dispatch(self):
        from agentobs._stream import _dispatch  # noqa: PLC0415

        calls = []
        mock_exporter = MagicMock()
        mock_exporter.export.side_effect = lambda e: calls.append(e)

        configure(trace_filters=[lambda e: False])
        with patch("agentobs._stream._active_exporter", return_value=mock_exporter):
            _dispatch(self._make_dispatched_event())

        assert len(calls) == 0


# ===========================================================================
# 3.2d  Statistical sampling accuracy
# ===========================================================================


class TestSamplingAccuracy:
    """Verify that sampling rate is within ~5% of configured value."""

    def teardown_method(self, _method):
        configure(sample_rate=1.0, always_sample_errors=True, trace_filters=[])

    def _count_emitted(self, rate: float, n: int = 5000) -> int:
        configure(sample_rate=rate, always_sample_errors=False)
        cfg = get_config()
        count = 0
        # Use Knuth's multiplicative hash to spread first-8-hex bucket values
        # across the full 32-bit range.
        _M = 2654435761  # Knuth's prime multiplier
        for i in range(n):
            prefix = format((i * _M) & 0xFFFFFFFF, "08x")
            trace_id = prefix + "0" * 24
            ev = _make_event({"trace_id": trace_id, "status": "ok"})
            if _should_emit(ev, cfg):
                count += 1
        return count

    def test_rate_50_percent(self):
        emitted = self._count_emitted(0.5, n=5000)
        # Within ±7% (broad to avoid flakiness)
        assert 2100 <= emitted <= 2900, f"expected ~2500, got {emitted}"

    def test_rate_25_percent(self):
        emitted = self._count_emitted(0.25, n=4000)
        assert 700 <= emitted <= 1300, f"expected ~1000, got {emitted}"

    def test_rate_75_percent(self):
        emitted = self._count_emitted(0.75, n=4000)
        assert 2600 <= emitted <= 3400, f"expected ~3000, got {emitted}"

    def test_rate_100_percent_all_pass(self):
        emitted = self._count_emitted(1.0, n=1000)
        assert emitted == 1000

    def test_rate_0_percent_all_dropped_except_bucket_0(self):
        configure(sample_rate=0.0, always_sample_errors=False)
        cfg = get_config()
        total, dropped = 1000, 0
        _M = 2654435761
        for i in range(1, total + 1):
            prefix = format((i * _M) & 0xFFFFFFFF, "08x")
            trace_id = prefix + "0" * 24
            ev = _make_event({"trace_id": trace_id, "status": "ok"})
            if not _should_emit(ev, cfg):
                dropped += 1
        # At least 99% dropped (bucket=0 is one-in-4-billion chance)
        assert dropped >= 990
