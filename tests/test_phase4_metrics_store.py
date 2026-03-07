"""tests/test_phase4_metrics_store.py — Exhaustive tests for Phase 4 changes.

Phase 4 covers:
- 4.1  agentobs/metrics.py — aggregate(), agent_success_rate(), llm_latency(),
                              tool_failure_rate(), token_usage()
- 4.2  agentobs/_store.py  — TraceStore ring buffer, get_trace(),
                              get_last_agent_run(), list_tool_calls(),
                              list_llm_calls(), clear()
       agentobs/config.py  — enable_trace_store, trace_store_size
       agentobs/_stream.py — _dispatch() wires store

Coverage target: ≥ 95 % of new Phase-4 code.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

import pytest

import agentobs
import agentobs.metrics as metrics
from agentobs import configure, get_config
from agentobs._store import TraceStore, _reset_store, get_store, get_trace, get_last_agent_run, list_tool_calls, list_llm_calls
from agentobs.event import Event, Tags
from agentobs.metrics import (
    LatencyStats,
    MetricsSummary,
    aggregate,
    agent_success_rate,
    llm_latency,
    tool_failure_rate,
    token_usage,
)
from agentobs.namespaces.trace import (
    CostBreakdown,
    GenAIOperationName,
    GenAISystem,
    ModelInfo,
    SpanKind,
    SpanPayload,
    TokenUsage,
)
from agentobs.types import EventType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRACE_A = "a" * 32
_TRACE_B = "b" * 32
_SPAN_ID = "c" * 16
_SPAN_ID2 = "d" * 16
_T0 = 1_000_000_000_000_000_000


def _make_span_event(
    trace_id: str = _TRACE_A,
    span_id: str = _SPAN_ID,
    operation: str = "chat",
    status: str = "ok",
    duration_ms: float = 100.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model_name: str | None = None,
    cost_usd: float = 0.0,
    event_type: EventType = EventType.TRACE_SPAN_COMPLETED,
) -> Event:
    """Build a minimal span Event for metrics/store testing."""
    payload: dict[str, Any] = {
        "span_id": span_id,
        "trace_id": trace_id,
        "span_name": "test",
        "operation": operation,
        "span_kind": "internal",
        "status": status,
        "start_time_unix_nano": _T0,
        "end_time_unix_nano": _T0 + int(duration_ms * 1_000_000),
        "duration_ms": duration_ms,
        "tool_calls": [],
        "reasoning_steps": [],
    }
    if input_tokens or output_tokens:
        payload["token_usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    if model_name:
        payload["model"] = {"system": "openai", "name": model_name}
    if cost_usd:
        payload["cost"] = {"input_cost_usd": cost_usd * 0.5, "output_cost_usd": cost_usd * 0.5, "total_cost_usd": cost_usd}
    return Event(
        event_type=event_type,
        source="test-agent@1.0.0",
        payload=payload,
        tags=Tags(env="test"),
        trace_id=trace_id,
        span_id=span_id,
    )


def _make_agent_completed_event(
    trace_id: str = _TRACE_A,
    duration_ms: float = 1000.0,
) -> Event:
    payload = {
        "agent_run_id": span_id_for(trace_id),
        "agent_name": "test-agent",
        "trace_id": trace_id,
        "root_span_id": span_id_for(trace_id),
        "total_steps": 1,
        "total_model_calls": 1,
        "total_tool_calls": 0,
        "total_token_usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        "total_cost": {"input_cost_usd": 0.001, "output_cost_usd": 0.001, "total_cost_usd": 0.002},
        "status": "ok",
        "start_time_unix_nano": _T0,
        "end_time_unix_nano": _T0 + int(duration_ms * 1_000_000),
        "duration_ms": duration_ms,
    }
    return Event(
        event_type=EventType.TRACE_AGENT_COMPLETED,
        source="test-agent@1.0.0",
        payload=payload,
        tags=Tags(env="test"),
        trace_id=trace_id,
    )


def span_id_for(trace_id: str) -> str:
    return (trace_id[:16])


# ===========================================================================
# 4.1  metrics — LatencyStats
# ===========================================================================


class TestLatencyStats:
    def test_empty(self):
        ls = LatencyStats._from_samples([])
        assert ls.min == 0.0
        assert ls.max == 0.0
        assert ls.p50 == 0.0
        assert ls.p95 == 0.0
        assert ls.p99 == 0.0

    def test_single(self):
        ls = LatencyStats._from_samples([42.0])
        assert ls.min == 42.0
        assert ls.max == 42.0
        assert ls.p50 == 42.0
        assert ls.p95 == 42.0
        assert ls.p99 == 42.0

    def test_two_values(self):
        ls = LatencyStats._from_samples([10.0, 20.0])
        assert ls.min == 10.0
        assert ls.max == 20.0

    def test_percentiles_ordered(self):
        samples = [float(i) for i in range(1, 101)]
        ls = LatencyStats._from_samples(samples)
        assert ls.p50 <= ls.p95 <= ls.p99

    def test_frozen(self):
        ls = LatencyStats(min=1.0, max=2.0, p50=1.5, p95=1.9, p99=2.0)
        with pytest.raises((AttributeError, TypeError)):
            ls.min = 99.0  # type: ignore


# ===========================================================================
# 4.1  metrics — aggregate() + helper functions
# ===========================================================================


class TestAggregate:
    def test_empty_events(self):
        result = aggregate([])
        assert result.trace_count == 0
        assert result.span_count == 0
        assert result.agent_success_rate == 1.0
        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.total_cost_usd == 0.0
        assert result.tool_failure_rate == 0.0

    def test_single_ok_span(self):
        ev = _make_span_event(operation="chat", status="ok")
        result = aggregate([ev])
        assert result.span_count == 1
        assert result.agent_success_rate == 1.0

    def test_error_span_reduces_success_rate(self):
        ok_ev = _make_span_event(trace_id=_TRACE_A, status="ok")
        err_ev = _make_span_event(
            trace_id=_TRACE_B, span_id=_SPAN_ID2, status="error",
            event_type=EventType.TRACE_SPAN_FAILED,
        )
        result = aggregate([ok_ev, err_ev])
        # Two traces: one ok, one error → 50% success
        assert result.trace_count == 2
        assert result.agent_success_rate == pytest.approx(0.5)

    def test_all_errors_zero_success_rate(self):
        events = [
            _make_span_event(trace_id=_TRACE_A, status="error",
                             event_type=EventType.TRACE_SPAN_FAILED),
            _make_span_event(trace_id=_TRACE_B, span_id=_SPAN_ID2, status="error",
                             event_type=EventType.TRACE_SPAN_FAILED),
        ]
        assert aggregate(events).agent_success_rate == 0.0

    def test_token_accumulation(self):
        events = [
            _make_span_event(trace_id=_TRACE_A, input_tokens=100, output_tokens=50),
            _make_span_event(trace_id=_TRACE_A, span_id=_SPAN_ID2, input_tokens=200, output_tokens=80),
        ]
        result = aggregate(events)
        assert result.total_input_tokens == 300
        assert result.total_output_tokens == 130

    def test_cost_accumulation(self):
        events = [
            _make_span_event(cost_usd=0.01),
            _make_span_event(span_id=_SPAN_ID2, cost_usd=0.02),
        ]
        result = aggregate(events)
        assert result.total_cost_usd == pytest.approx(0.03)

    def test_llm_latency_populated(self):
        events = [
            _make_span_event(operation="chat", duration_ms=100.0),
            _make_span_event(span_id=_SPAN_ID2, operation="chat", duration_ms=200.0),
        ]
        result = aggregate(events)
        assert result.llm_latency_ms.min == pytest.approx(100.0)
        assert result.llm_latency_ms.max == pytest.approx(200.0)

    def test_non_llm_operations_not_counted_as_llm(self):
        events = [_make_span_event(operation="tool_call", duration_ms=50.0)]
        result = aggregate(events)
        assert result.llm_latency_ms.min == 0.0

    def test_tool_failure_rate(self):
        events = [
            _make_span_event(operation="tool_call", status="ok"),
            _make_span_event(span_id=_SPAN_ID2, operation="tool_call", status="error",
                             event_type=EventType.TRACE_SPAN_FAILED),
        ]
        result = aggregate(events)
        assert result.tool_failure_rate == pytest.approx(0.5)

    def test_tool_failure_rate_no_tools(self):
        events = [_make_span_event(operation="chat")]
        result = aggregate(events)
        assert result.tool_failure_rate == 0.0

    def test_token_by_model(self):
        events = [
            _make_span_event(model_name="gpt-4o", input_tokens=100, output_tokens=50),
            _make_span_event(span_id=_SPAN_ID2, model_name="gpt-3.5-turbo", input_tokens=200, output_tokens=100),
        ]
        result = aggregate(events)
        assert "gpt-4o" in result.token_usage_by_model
        assert result.token_usage_by_model["gpt-4o"]["input_tokens"] == 100
        assert result.token_usage_by_model["gpt-3.5-turbo"]["output_tokens"] == 100

    def test_agent_completed_provides_trace_duration(self):
        events = [
            _make_agent_completed_event(trace_id=_TRACE_A, duration_ms=500.0),
            _make_agent_completed_event(trace_id=_TRACE_B, duration_ms=1500.0),
        ]
        result = aggregate(events)
        assert result.avg_trace_duration_ms == pytest.approx(1000.0)
        assert result.p50_trace_duration_ms == pytest.approx(1000.0, rel=0.05)

    def test_non_span_events_not_counted_as_spans(self):
        agent_ev = _make_agent_completed_event()
        result = aggregate([agent_ev])
        assert result.span_count == 0

    def test_iterable_not_just_list(self):
        # Should accept any iterable, e.g. a generator.
        def gen():
            yield _make_span_event(operation="chat")
        result = aggregate(gen())
        assert result.span_count == 1


class TestHelperFunctions:
    def test_agent_success_rate(self):
        events = [
            _make_span_event(trace_id=_TRACE_A, status="ok"),
            _make_span_event(trace_id=_TRACE_B, span_id=_SPAN_ID2, status="error",
                             event_type=EventType.TRACE_SPAN_FAILED),
        ]
        assert agent_success_rate(events) == pytest.approx(0.5)

    def test_llm_latency_empty(self):
        ls = llm_latency([])
        assert ls.p95 == 0.0

    def test_llm_latency_values(self):
        events = [_make_span_event(operation="chat", duration_ms=300.0)]
        ls = llm_latency(events)
        assert ls.min == pytest.approx(300.0)

    def test_tool_failure_rate_all_ok(self):
        events = [_make_span_event(operation="tool_call", status="ok")]
        assert tool_failure_rate(events) == 0.0

    def test_token_usage_dict(self):
        events = [_make_span_event(model_name="gpt-4o", input_tokens=50, output_tokens=30)]
        result = token_usage(events)
        assert result["gpt-4o"]["input_tokens"] == 50


class TestMetricsModuleExport:
    def test_module_accessible_from_agentobs(self):
        import agentobs
        assert hasattr(agentobs, "metrics")
        result = agentobs.metrics.aggregate([])
        assert isinstance(result, MetricsSummary)

    def test_metrics_imported_directly(self):
        from agentobs.metrics import aggregate as agg
        assert callable(agg)


# ===========================================================================
# 4.2  TraceStore — ring buffer
# ===========================================================================


class TestTraceStoreBasics:
    def setup_method(self):
        self.store = TraceStore(max_traces=10)

    def test_empty_store(self):
        assert len(self.store) == 0
        assert self.store.get_trace(_TRACE_A) is None
        assert self.store.get_last_agent_run() is None

    def test_record_single_event(self):
        ev = _make_span_event(trace_id=_TRACE_A)
        self.store.record(ev)
        result = self.store.get_trace(_TRACE_A)
        assert result is not None
        assert len(result) == 1
        assert result[0] is ev

    def test_record_multiple_traces(self):
        self.store.record(_make_span_event(trace_id=_TRACE_A))
        self.store.record(_make_span_event(trace_id=_TRACE_B, span_id=_SPAN_ID2))
        assert len(self.store) == 2
        assert self.store.get_trace(_TRACE_A) is not None
        assert self.store.get_trace(_TRACE_B) is not None

    def test_get_trace_returns_copy(self):
        ev = _make_span_event(trace_id=_TRACE_A)
        self.store.record(ev)
        result1 = self.store.get_trace(_TRACE_A)
        result2 = self.store.get_trace(_TRACE_A)
        assert result1 is not result2  # must be a new list each time

    def test_get_nonexistent_trace_returns_none(self):
        assert self.store.get_trace("z" * 32) is None

    def test_clear(self):
        self.store.record(_make_span_event(trace_id=_TRACE_A))
        self.store.clear()
        assert len(self.store) == 0
        assert self.store.get_trace(_TRACE_A) is None
        assert self.store.get_last_agent_run() is None

    def test_invalid_max_traces(self):
        with pytest.raises(ValueError):
            TraceStore(max_traces=0)


class TestTraceStoreRingBuffer:
    def test_evicts_oldest_trace_when_full(self):
        store = TraceStore(max_traces=3)
        trace_ids = ["a" * 31 + str(i) for i in range(4)]
        span_ids = ["b" * 15 + str(i) for i in range(4)]
        for i, (tid, sid) in enumerate(zip(trace_ids, span_ids)):
            store.record(_make_span_event(trace_id=tid, span_id=sid))
        # After inserting 4 traces into a buffer of size 3, the first trace
        # (trace_ids[0]) should be evicted.
        assert store.get_trace(trace_ids[0]) is None
        for tid in trace_ids[1:]:
            assert store.get_trace(tid) is not None

    def test_existing_trace_not_evicted_on_update(self):
        store = TraceStore(max_traces=2)
        store.record(_make_span_event(trace_id=_TRACE_A))
        store.record(_make_span_event(trace_id=_TRACE_B, span_id=_SPAN_ID2))
        # Adding a second event to an *existing* trace must not evict anything.
        store.record(_make_span_event(trace_id=_TRACE_A, span_id=_SPAN_ID2))
        assert len(store) == 2
        assert store.get_trace(_TRACE_A) is not None
        assert store.get_trace(_TRACE_B) is not None

    def test_evicts_lru_trace(self):
        """After re-accessing TRACE_A, TRACE_B should be evicted next."""
        store = TraceStore(max_traces=2)
        store.record(_make_span_event(trace_id=_TRACE_A))
        store.record(_make_span_event(trace_id=_TRACE_B, span_id=_SPAN_ID2))
        # Access TRACE_A again — it moves to "most recently used" end.
        store.record(_make_span_event(trace_id=_TRACE_A, span_id=_SPAN_ID2))
        # Insert a new trace — TRACE_B (now the oldest) should be evicted.
        trace_c = "c" * 32
        store.record(_make_span_event(trace_id=trace_c, span_id="e" * 16))
        assert store.get_trace(_TRACE_A) is not None
        assert store.get_trace(_TRACE_B) is None
        assert store.get_trace(trace_c) is not None


class TestTraceStoreLastAgentRun:
    def test_get_last_agent_run_without_any_recorded(self):
        store = TraceStore()
        assert store.get_last_agent_run() is None

    def test_get_last_agent_run_after_agent_completed_event(self):
        store = TraceStore()
        span_ev = _make_span_event(trace_id=_TRACE_A)
        agent_ev = _make_agent_completed_event(trace_id=_TRACE_A)
        store.record(span_ev)
        store.record(agent_ev)
        result = store.get_last_agent_run()
        assert result is not None
        assert len(result) == 2

    def test_last_agent_run_updates_on_new_completion(self):
        store = TraceStore()
        store.record(_make_agent_completed_event(trace_id=_TRACE_A))
        store.record(_make_agent_completed_event(trace_id=_TRACE_B))
        result = store.get_last_agent_run()
        # Most recent agent run is TRACE_B
        assert result is not None
        tid = result[0].payload.get("trace_id") or getattr(result[0], "trace_id", None)
        assert tid == _TRACE_B


class TestTraceStoreListCalls:
    def setup_method(self):
        self.store = TraceStore()

    def test_list_tool_calls_empty(self):
        assert self.store.list_tool_calls(_TRACE_A) == []

    def test_list_tool_calls_no_match(self):
        self.store.record(_make_span_event(operation="chat"))
        assert self.store.list_tool_calls(_TRACE_A) == []

    def test_list_tool_calls_with_matching_spans(self):
        self.store.record(_make_span_event(operation="tool_call"))
        result = self.store.list_tool_calls(_TRACE_A)
        assert len(result) == 1
        assert isinstance(result[0], SpanPayload)
        assert result[0].operation == "tool_call"

    def test_list_llm_calls_empty(self):
        assert self.store.list_llm_calls(_TRACE_A) == []

    def test_list_llm_calls_with_matching_spans(self):
        self.store.record(_make_span_event(operation="chat"))
        result = self.store.list_llm_calls(_TRACE_A)
        assert len(result) == 1
        assert isinstance(result[0], SpanPayload)

    def test_list_llm_calls_excludes_tool_calls(self):
        self.store.record(_make_span_event(operation="tool_call"))
        assert self.store.list_llm_calls(_TRACE_A) == []

    def test_list_tool_calls_excludes_llm_calls(self):
        self.store.record(_make_span_event(operation="chat"))
        assert self.store.list_tool_calls(_TRACE_A) == []

    def test_list_calls_sorted_by_start_time(self):
        ev1 = _make_span_event(operation="tool_call", duration_ms=100.0)
        ev2_payload = dict(ev1.payload)
        ev2_payload["span_id"] = _SPAN_ID2
        ev2_payload["start_time_unix_nano"] = _T0 + 200_000_000  # later
        ev2_payload["end_time_unix_nano"] = _T0 + 300_000_000
        from agentobs.event import Event, Tags  # noqa: PLC0415
        ev2 = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="test-agent@1.0.0",
            payload=ev2_payload,
            tags=Tags(env="test"),
        )
        self.store.record(ev2)
        self.store.record(ev1)
        result = self.store.list_tool_calls(_TRACE_A)
        assert result[0].start_time_unix_nano <= result[-1].start_time_unix_nano


class TestTraceStoreSingleton:
    def test_module_level_functions_use_singleton(self):
        _reset_store()
        store = get_store()
        ev = _make_span_event(trace_id=_TRACE_A)
        store.record(ev)
        assert get_trace(_TRACE_A) is not None

    def test_reset_store_creates_new_instance(self):
        store_before = get_store()
        _reset_store(max_traces=200)
        store_after = get_store()
        assert store_before is not store_after


# ===========================================================================
# 4.2  Config fields + env var
# ===========================================================================


class TestTraceStoreConfig:
    def teardown_method(self):
        # Reset config to clean state after each test.
        configure(enable_trace_store=False, trace_store_size=100)
        _reset_store()

    def test_default_enable_trace_store_is_false(self):
        cfg = get_config()
        assert cfg.enable_trace_store is False

    def test_default_trace_store_size_is_100(self):
        cfg = get_config()
        assert cfg.trace_store_size == 100

    def test_configure_sets_enable_trace_store(self):
        configure(enable_trace_store=True)
        assert get_config().enable_trace_store is True
        configure(enable_trace_store=False)

    def test_configure_sets_trace_store_size(self):
        configure(trace_store_size=50)
        assert get_config().trace_store_size == 50
        configure(trace_store_size=100)

    def test_env_var_enables_trace_store(self, monkeypatch):
        monkeypatch.setenv("AGENTOBS_ENABLE_TRACE_STORE", "1")
        from agentobs.config import AgentOBSConfig, _load_from_env, _config  # noqa: PLC0415
        _config.enable_trace_store = False  # reset
        _load_from_env()
        assert _config.enable_trace_store is True
        _config.enable_trace_store = False  # cleanup

    def test_env_var_true_string(self, monkeypatch):
        monkeypatch.setenv("AGENTOBS_ENABLE_TRACE_STORE", "true")
        from agentobs.config import _config, _load_from_env  # noqa: PLC0415
        _config.enable_trace_store = False
        _load_from_env()
        assert _config.enable_trace_store is True
        _config.enable_trace_store = False


# ===========================================================================
# 4.2  TraceStore wired into _stream._dispatch()
# ===========================================================================


class TestTraceStoreDispatchIntegration:
    def setup_method(self):
        configure(enable_trace_store=True, on_export_error="raise")
        _reset_store()

    def teardown_method(self):
        configure(enable_trace_store=False, on_export_error="warn")
        _reset_store()

    def test_dispatched_span_appears_in_store(self):
        from agentobs import tracer  # noqa: PLC0415
        from agentobs._stream import _reset_exporter  # noqa: PLC0415

        configure(exporter="console", enable_trace_store=True)
        _reset_exporter()
        _reset_store()

        captured_events = []
        with patch("agentobs._store.TraceStore.record", side_effect=lambda e: captured_events.append(e)):
            with tracer.span("test-span", operation="chat") as span:
                trace_id = span.trace_id

        # At least one event was captured by the mocked store
        assert len(captured_events) >= 1

    def test_store_disabled_does_not_record(self):
        configure(enable_trace_store=False)
        _reset_store()

        from agentobs import tracer  # noqa: PLC0415
        from agentobs._stream import _reset_exporter  # noqa: PLC0415
        _reset_exporter()

        with patch("agentobs._store.TraceStore.record") as mock_record:
            with tracer.span("test-span"):
                pass
            mock_record.assert_not_called()


# ===========================================================================
# 4.2  Module-level convenience functions
# ===========================================================================


class TestModuleLevelStoreFunctions:
    def setup_method(self):
        _reset_store()

    def test_get_trace_returns_none_when_empty(self):
        assert get_trace(_TRACE_A) is None

    def test_get_last_agent_run_returns_none_when_empty(self):
        assert get_last_agent_run() is None

    def test_list_tool_calls_returns_empty_when_no_trace(self):
        assert list_tool_calls(_TRACE_A) == []

    def test_list_llm_calls_returns_empty_when_no_trace(self):
        assert list_llm_calls(_TRACE_A) == []

    def test_functions_accessible_from_agentobs_namespace(self):
        import agentobs  # noqa: PLC0415
        assert callable(agentobs.get_trace)
        assert callable(agentobs.get_last_agent_run)
        assert callable(agentobs.list_tool_calls)
        assert callable(agentobs.list_llm_calls)
