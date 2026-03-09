"""Phase 4 — Agent Instrumentation tests.

Covers:
* tracer.agent_run() / tracer.agent_step() context manager protocol
* AgentRunContext / AgentStepContext record accumulation
* Event emission: TRACE_AGENT_STEP + TRACE_AGENT_COMPLETED
* AgentRunPayload aggregation (tokens, cost, step count, model calls, tool calls)
* agent_run_id / trace_id / parent_span_id propagation
* Step index increment
* Async context manager (async with agent_run / agent_step)
* Error capture on exception inside the block
* Nested runs (run-stack isolation)
* agent_step outside agent_run → RuntimeError
* Exception paths in emit (error-handler branch in __exit__)
* _stream.emit_agent_step and emit_agent_run directly
* _stream._handle_export_error policy="raise" and get_config failure branches
* _stream._reset_exporter when close() raises
* _stream._active_exporter double-check inside lock
* _stream._build_event with org_id and span_id
* _stream._should_emit ValueError (invalid trace_id hex) and no-trace_id fallback
* _stream emit_span trace collector _record_span exception swallowing
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import agentobs._stream as stream_mod
from agentobs._span import (
    AgentRunContext,
    AgentRunContextManager,
    AgentStepContext,
    AgentStepContextManager,
    SpanContextManager,
    _run_stack,
    _run_stack_var,
    _span_stack_var,
)
from agentobs._stream import (
    _build_event,
    _handle_export_error,
    _reset_exporter,
    _should_emit,
    emit_agent_run,
    emit_agent_step,
    emit_span,
)
from agentobs._tracer import tracer
from agentobs.config import AgentOBSConfig, configure, get_config
from agentobs.event import Event
from agentobs.namespaces.trace import (
    CostBreakdown,
    DecisionPoint,
    ReasoningStep,
    ToolCall,
    TokenUsage,
)
from agentobs.types import EventType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CapturingExporter:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.export_calls = 0

    def export(self, event: Event) -> None:
        self.export_calls += 1
        self.events.append(event)

    def flush(self) -> None:
        ...
    def close(self) -> None:
        ...
def _install_exporter() -> _CapturingExporter:
    _reset_exporter()
    cap = _CapturingExporter()
    stream_mod._cached_exporter = cap
    return cap


def _clean_stacks() -> None:
    _span_stack_var.set(())
    _run_stack_var.set(())


def _token_usage(n: int = 10) -> TokenUsage:
    return TokenUsage(input_tokens=n, output_tokens=n * 2, total_tokens=n * 3)


def _cost_breakdown(total: float = 0.003) -> CostBreakdown:
    return CostBreakdown(
        input_cost_usd=total * 0.4,
        output_cost_usd=total * 0.6,
        total_cost_usd=total,
    )


# ===========================================================================
# 1. agent_run context manager basics
# ===========================================================================


@pytest.mark.unit
class TestAgentRunContextManager:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_returns_agent_run_context(self) -> None:
        with tracer.agent_run("test-agent") as run:
            assert isinstance(run, AgentRunContext)

    def test_agent_name_stored(self) -> None:
        with tracer.agent_run("my-bot") as run:
            assert run.agent_name == "my-bot"

    def test_run_has_valid_ids(self) -> None:
        with tracer.agent_run("id-test") as run:
            assert len(run.agent_run_id) == 16
            int(run.agent_run_id, 16)
            assert len(run.trace_id) == 32
            int(run.trace_id, 16)
            assert len(run.root_span_id) == 16
            int(run.root_span_id, 16)

    def test_run_stack_push_and_pop(self) -> None:
        assert _run_stack() == ()
        with tracer.agent_run("stack-test"):
            assert len(_run_stack()) == 1
        assert _run_stack() == ()

    def test_emits_agent_completed_event(self) -> None:
        with tracer.agent_run("emit-test"):
            ...
        assert len(self.cap.events) == 1
        assert self.cap.events[0].event_type == EventType.TRACE_AGENT_COMPLETED

    def test_completed_payload_has_agent_name(self) -> None:
        with tracer.agent_run("named-agent"):
            ...
        payload = self.cap.events[0].payload
        assert payload["agent_name"] == "named-agent"

    def test_status_ok_on_clean_exit(self) -> None:
        with tracer.agent_run("clean-exit"):
            ...
        assert self.cap.events[0].payload["status"] == "ok"

    def test_status_error_on_exception(self) -> None:
        with pytest.raises(ValueError):
            with tracer.agent_run("error-agent"):
                raise ValueError("boom")
        payload = self.cap.events[0].payload
        assert payload["status"] == "error"

    def test_duration_ms_positive(self) -> None:
        with tracer.agent_run("duration-test"):
            ...
        duration = self.cap.events[0].payload["duration_ms"]
        assert isinstance(duration, float)
        assert duration >= 0.0

    def test_run_produces_unique_ids_across_runs(self) -> None:
        ids = []
        for _ in range(5):
            with tracer.agent_run("unique") as run:
                ids.append(run.agent_run_id)
        assert len(set(ids)) == 5


# ===========================================================================
# 2. agent_step context manager basics
# ===========================================================================


@pytest.mark.unit
class TestAgentStepContextManager:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_step_outside_run_raises(self) -> None:
        with pytest.raises(RuntimeError, match="agent_run"):
            with tracer.agent_step("orphan"):
                ...
    def test_returns_agent_step_context(self) -> None:
        with tracer.agent_run("parent"):
            with tracer.agent_step("step-1") as step:
                assert isinstance(step, AgentStepContext)

    def test_step_name_stored(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("my-step") as step:
                assert step.step_name == "my-step"

    def test_step_inherits_agent_run_id(self) -> None:
        with tracer.agent_run("parent") as run:
            with tracer.agent_step("step") as step:
                assert step.agent_run_id == run.agent_run_id

    def test_step_inherits_trace_id(self) -> None:
        with tracer.agent_run("parent") as run:
            with tracer.agent_step("step") as step:
                assert step.trace_id == run.trace_id

    def test_step_index_starts_at_zero(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("first") as step:
                assert step.step_index == 0

    def test_step_indexes_increment(self) -> None:
        indexes = []
        with tracer.agent_run("run"):
            for i in range(3):
                with tracer.agent_step(f"step-{i}") as step:
                    indexes.append(step.step_index)
        assert indexes == [0, 1, 2]

    def test_step_emits_agent_step_event(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("step"):
                ...
        # run emits 1 event (COMPLETED), step emits 1 event (STEP)
        step_events = [e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP]
        assert len(step_events) == 1

    def test_step_status_ok_on_clean_exit(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("step"):
                ...
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        assert step_event.payload["status"] == "ok"

    def test_step_status_error_on_exception(self) -> None:
        with pytest.raises(RuntimeError):
            with tracer.agent_run("run"):
                with tracer.agent_step("failing-step"):
                    raise RuntimeError("step failed")
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        assert step_event.payload["status"] == "error"

    def test_step_set_attribute(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("step") as step:
                step.set_attribute("key", "val")
        assert step.attributes["key"] == "val"

    def test_step_invalid_attribute_key_raises(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("step") as step:
                with pytest.raises(ValueError):
                    step.set_attribute("", "val")

    def test_step_record_error(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("step") as step:
                step.record_error(RuntimeError("manual err"))
        assert step.status == "error"
        assert step.error == "manual err"


# ===========================================================================
# 3. Run+Step event emission and aggregation
# ===========================================================================


@pytest.mark.unit
class TestAgentRunAggregation:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_total_steps_matches(self) -> None:
        with tracer.agent_run("agg-test"):
            for i in range(3):
                with tracer.agent_step(f"s{i}"):
                    ...
        run_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED)
        assert run_event.payload["total_steps"] == 3

    def test_total_steps_zero_when_no_steps(self) -> None:
        with tracer.agent_run("no-steps"):
            ...
        run_payload = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED).payload
        assert run_payload["total_steps"] == 0

    def test_token_aggregation(self) -> None:
        with tracer.agent_run("tokens"):
            with tracer.agent_step("s1") as step:
                step.token_usage = _token_usage(10)
            with tracer.agent_step("s2") as step:
                step.token_usage = _token_usage(20)
        run_payload = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED).payload
        tu = run_payload["total_token_usage"]
        assert tu["input_tokens"] == 30  # 10+20
        assert tu["output_tokens"] == 60  # 20+40
        assert tu["total_tokens"] == 90  # 30+60

    def test_cost_aggregation(self) -> None:
        with tracer.agent_run("costs"):
            with tracer.agent_step("s1") as step:
                step.cost = _cost_breakdown(0.010)
            with tracer.agent_step("s2") as step:
                step.cost = _cost_breakdown(0.020)
        run_payload = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED).payload
        cost = run_payload["total_cost"]
        assert abs(cost["total_cost_usd"] - 0.030) < 1e-9

    def test_model_call_count(self) -> None:
        with tracer.agent_run("mcalls"):
            with tracer.agent_step("s1") as step:
                step.token_usage = _token_usage()
            with tracer.agent_step("s2") as step:
                pass  # no token_usage
        run_payload = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED).payload
        assert run_payload["total_model_calls"] == 1

    def test_tool_call_count(self) -> None:
        tool = ToolCall(tool_call_id="tc-1", function_name="search", status="success")
        with tracer.agent_run("tcalls"):
            with tracer.agent_step("s1") as step:
                step.tool_calls.append(tool)
                step.tool_calls.append(tool)
            with tracer.agent_step("s2") as step:
                step.tool_calls.append(tool)
        run_payload = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED).payload
        assert run_payload["total_tool_calls"] == 3

    def test_agent_run_id_in_both_events(self) -> None:
        with tracer.agent_run("id-check"):
            with tracer.agent_step("step"):
                ...
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        run_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED)
        assert step_event.payload["agent_run_id"] == run_event.payload["agent_run_id"]

    def test_trace_id_consistent_across_events(self) -> None:
        with tracer.agent_run("trace-check"):
            with tracer.agent_step("s1"):
                ...
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        run_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED)
        assert step_event.payload["trace_id"] == run_event.payload["trace_id"]


# ===========================================================================
# 4. Span inside agent_run
# ===========================================================================


@pytest.mark.unit
class TestSpanInsideAgentRun:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_span_inherits_agent_run_id(self) -> None:
        with tracer.agent_run("run") as run:
            with tracer.span("llm-call") as span:
                assert span.agent_run_id == run.agent_run_id

    def test_span_inherits_trace_id(self) -> None:
        with tracer.agent_run("run") as run:
            with tracer.span("llm-call") as span:
                assert span.trace_id == run.trace_id

    def test_span_inside_step_no_implicit_parent(self) -> None:
        """Spans inside agent_step do NOT inherit the step's span_id as parent.

        The AgentStepContext lives on the run-stack (`_run_stack_var`), while
        SpanContextManager resolves its parent from the span-stack
        (`_span_stack_var`).  When the span-stack is empty the parent_span_id
        is None.  The trace_id is still inherited from the enclosing run.
        """
        with tracer.agent_run("run") as run:
            with tracer.agent_step("step") as step:
                with tracer.span("llm") as span:
                    assert span.parent_span_id is None
                    assert span.trace_id == run.trace_id

    def test_all_spans_same_trace_id(self) -> None:
        trace_ids = set()
        with tracer.agent_run("run") as run:
            with tracer.agent_step("s1"):
                with tracer.span("llm") as span:
                    trace_ids.add(span.trace_id)
            trace_ids.add(run.trace_id)
        assert len(trace_ids) == 1


# ===========================================================================
# 5. Async context manager protocol
# ===========================================================================


@pytest.mark.asyncio
class TestAgentAsyncContextManagers:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    async def test_async_agent_run_emits_event(self) -> None:
        async with tracer.agent_run("async-run") as run:
            assert isinstance(run, AgentRunContext)
        assert any(e.event_type == EventType.TRACE_AGENT_COMPLETED for e in self.cap.events)

    async def test_async_agent_step_emits_event(self) -> None:
        async with tracer.agent_run("async-run"):
            async with tracer.agent_step("async-step") as step:
                assert isinstance(step, AgentStepContext)
        assert any(e.event_type == EventType.TRACE_AGENT_STEP for e in self.cap.events)

    async def test_async_step_without_run_raises(self) -> None:
        _clean_stacks()
        with pytest.raises(RuntimeError, match="agent_run"):
            async with tracer.agent_step("orphan"):
                ...
    async def test_async_run_captures_exception(self) -> None:
        with pytest.raises(ValueError):
            async with tracer.agent_run("async-err"):
                raise ValueError("async fail")
        run_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED)
        assert run_event.payload["status"] == "error"

    async def test_concurrent_async_runs_isolated(self) -> None:
        results: dict[str, str] = {}

        async def run_agent(name: str) -> None:
            async with tracer.agent_run(name) as run:
                await asyncio.sleep(0)
                results[name] = run.agent_run_id

        await asyncio.gather(run_agent("alpha"), run_agent("beta"), run_agent("gamma"))
        assert len(set(results.values())) == 3


# ===========================================================================
# 6. Nested agent runs (run stack isolation)
# ===========================================================================


@pytest.mark.unit
class TestNestedAgentRuns:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_nested_runs_have_different_ids(self) -> None:
        with tracer.agent_run("outer") as outer:
            with tracer.agent_run("inner") as inner:
                assert outer.agent_run_id != inner.agent_run_id

    def test_inner_step_gets_inner_run_id(self) -> None:
        with tracer.agent_run("outer"):
            with tracer.agent_run("inner") as inner:
                with tracer.agent_step("step") as step:
                    assert step.agent_run_id == inner.agent_run_id

    def test_run_stack_depth(self) -> None:
        with tracer.agent_run("outer"):
            assert len(_run_stack()) == 1
            with tracer.agent_run("inner"):
                assert len(_run_stack()) == 2
            assert len(_run_stack()) == 1
        assert len(_run_stack()) == 0

    def test_each_run_emits_its_own_completed_event(self) -> None:
        with tracer.agent_run("outer"):
            with tracer.agent_run("inner"):
                ...
        completed = [e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED]
        assert len(completed) == 2


# ===========================================================================
# 7. AgentStepContext richdata setters and to_agent_step_payload
# ===========================================================================


@pytest.mark.unit
class TestAgentStepContextData:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_step_with_model(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("s", operation="chat") as step:
                step.model = "gpt-4o"
        # model should appear in payload
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        assert step_event.payload.get("model") is not None

    def test_step_with_reasoning_steps(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("s") as step:
                step.reasoning_steps.append(
                    ReasoningStep(step_index=0, reasoning_tokens=120)
                )
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        assert len(step_event.payload["reasoning_steps"]) == 1

    def test_step_with_decision_points(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("s") as step:
                step.decision_points.append(
                    DecisionPoint(
                        decision_id="d1",
                        decision_type="tool_selection",
                        options_considered=["search", "query"],
                        chosen_option="search",
                    )
                )
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        assert len(step_event.payload["decision_points"]) == 1

    def test_step_with_tool_calls(self) -> None:
        tool = ToolCall(tool_call_id="tc-1", function_name="lookup", status="success")
        with tracer.agent_run("run"):
            with tracer.agent_step("s") as step:
                step.tool_calls.append(tool)
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        assert len(step_event.payload["tool_calls"]) == 1

    def test_step_custom_operation(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("s", operation="execute_tool") as step:
                ...
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        assert step_event.payload["operation"] == "execute_tool"

    def test_step_token_usage_in_payload(self) -> None:
        with tracer.agent_run("run"):
            with tracer.agent_step("s") as step:
                step.token_usage = _token_usage(5)
        step_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP)
        tu = step_event.payload["token_usage"]
        assert tu["input_tokens"] == 5


# ===========================================================================
# 8. AgentRunContext.to_agent_run_payload — edge cases
# ===========================================================================


@pytest.mark.unit
class TestAgentRunPayloadEdgeCases:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_termination_reason_none_by_default(self) -> None:
        with tracer.agent_run("run") as run:
            ...
        assert run.termination_reason is None

    def test_termination_reason_stored(self) -> None:
        with tracer.agent_run("run") as run:
            run.termination_reason = "max_steps_exceeded"
        assert run.termination_reason == "max_steps_exceeded"

    def test_error_message_propagated(self) -> None:
        with pytest.raises(ValueError):
            with tracer.agent_run("err-run"):
                raise ValueError("fail message")
        run_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED)
        # AgentRunPayload.to_dict() captures error status; no separate "error" field
        assert run_event.payload["status"] == "error"

    def test_step_not_registered_after_exception(self) -> None:
        """Steps that raise still get registered and their error status reflected."""
        with pytest.raises(OSError):
            with tracer.agent_run("run"):
                with tracer.agent_step("failing"):
                    raise OSError("io")
        run_event = next(e for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED)
        # run should report 1 step
        assert run_event.payload["total_steps"] == 1


# ===========================================================================
# 9. emit_agent_step / emit_agent_run direct calls via _stream
# ===========================================================================


@pytest.mark.unit
class TestStreamEmitFunctions:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def _make_run_ctx(self, name: str = "test-run") -> AgentRunContext:
        ctx = AgentRunContext(
            agent_name=name,
            agent_run_id="a" * 16,
            trace_id="b" * 32,
            root_span_id="c" * 16,
        )
        ctx.end()
        return ctx

    def _make_step_ctx(self, step_name: str = "step") -> AgentStepContext:
        ctx = AgentStepContext(
            step_name=step_name,
            agent_run_id="a" * 16,
            step_index=0,
            span_id="d" * 16,
            trace_id="b" * 32,
        )
        ctx.end()
        return ctx

    def test_emit_agent_run_exports_event(self) -> None:
        run = self._make_run_ctx()
        emit_agent_run(run)
        assert len(self.cap.events) == 1
        assert self.cap.events[0].event_type == EventType.TRACE_AGENT_COMPLETED

    def test_emit_agent_step_exports_event(self) -> None:
        step = self._make_step_ctx()
        emit_agent_step(step)
        assert len(self.cap.events) == 1
        assert self.cap.events[0].event_type == EventType.TRACE_AGENT_STEP

    def test_emit_agent_run_event_trace_id(self) -> None:
        run = self._make_run_ctx()
        emit_agent_run(run)
        assert self.cap.events[0].trace_id == "b" * 32

    def test_emit_agent_step_event_span_id(self) -> None:
        step = self._make_step_ctx()
        emit_agent_step(step)
        assert self.cap.events[0].span_id == "d" * 16


# ===========================================================================
# 10. _stream._handle_export_error branches
# ===========================================================================


@pytest.mark.unit
class TestHandleExportError:
    def setup_method(self) -> None:
        configure(on_export_error="warn")

    def teardown_method(self) -> None:
        configure(on_export_error="warn")
        _reset_exporter()

    def test_policy_drop_silences_error(self) -> None:
        configure(on_export_error="drop")
        # Should not raise or warn
        _handle_export_error(RuntimeError("dropped"))

    def test_policy_warn_emits_warning(self) -> None:
        configure(on_export_error="warn")
        with pytest.warns(UserWarning, match="export error"):
            _handle_export_error(RuntimeError("warned"))

    def test_policy_raise_re_raises(self) -> None:
        configure(on_export_error="raise")
        with pytest.raises(RuntimeError, match="re-raised"):
            _handle_export_error(RuntimeError("re-raised"))

    def test_broken_config_fallback_to_warn(self) -> None:
        """When get_config() itself raises, the fallback policy is 'warn'."""
        with patch("agentobs._stream.get_config", side_effect=Exception("config broken")):
            with pytest.warns(UserWarning):
                _handle_export_error(RuntimeError("fallback-warn"))


# ===========================================================================
# 11. _stream._reset_exporter when close() raises
# ===========================================================================


@pytest.mark.unit
class TestResetExporterErrorHandling:
    def setup_method(self) -> None:
        configure(on_export_error="drop")

    def teardown_method(self) -> None:
        configure(on_export_error="warn")
        _reset_exporter()

    def test_reset_swallows_close_exception(self) -> None:
        bad_exporter = MagicMock()
        bad_exporter.close.side_effect = OSError("disk full")
        stream_mod._cached_exporter = bad_exporter
        # Should not raise
        _reset_exporter()
        assert stream_mod._cached_exporter is None


# ===========================================================================
# 12. _stream._active_exporter — double-check inside lock
# ===========================================================================


@pytest.mark.unit
class TestActiveExporterCaching:
    def setup_method(self) -> None:
        _reset_exporter()
        configure(exporter="console")

    def teardown_method(self) -> None:
        _reset_exporter()
        configure(exporter="console")

    def test_exporter_cached_after_first_call(self) -> None:
        from agentobs._stream import _active_exporter
        exp1 = _active_exporter()
        exp2 = _active_exporter()
        assert exp1 is exp2

    def test_reset_clears_cache(self) -> None:
        from agentobs._stream import _active_exporter
        exp1 = _active_exporter()
        _reset_exporter()
        exp2 = _active_exporter()
        # After reset a new instance is created
        assert exp1 is not exp2


# ===========================================================================
# 13. _stream._build_event — org_id and span_id branches
# ===========================================================================


@pytest.mark.unit
class TestBuildEvent:
    def setup_method(self) -> None:
        configure(org_id=None)

    def teardown_method(self) -> None:
        configure(org_id=None)

    def test_build_event_with_org_id(self) -> None:
        configure(org_id="my-org")
        event = _build_event(
            event_type=EventType.TRACE_AGENT_STEP,
            payload_dict={"status": "ok"},
            span_id="d" * 16,
            trace_id="e" * 32,
        )
        assert event.org_id == "my-org"

    def test_build_event_without_org_id(self) -> None:
        configure(org_id=None)
        event = _build_event(
            event_type=EventType.TRACE_AGENT_STEP,
            payload_dict={"status": "ok"},
        )
        assert event.org_id is None

    def test_build_event_with_span_id(self) -> None:
        event = _build_event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            payload_dict={"status": "ok"},
            span_id="f" * 16,
            trace_id="g" * 32,
        )
        assert event.span_id == "f" * 16

    def test_build_event_without_span_id(self) -> None:
        event = _build_event(
            event_type=EventType.TRACE_AGENT_COMPLETED,
            payload_dict={"status": "ok"},
            trace_id="h" * 32,
        )
        # span_id is not set when not passed
        assert event.span_id is None

    def test_build_event_with_parent_span_id(self) -> None:
        event = _build_event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            payload_dict={"status": "ok"},
            span_id="a" * 16,
            trace_id="b" * 32,
            parent_span_id="c" * 16,
        )
        assert event.parent_span_id == "c" * 16


# ===========================================================================
# 14. _stream._should_emit — ValueError and no-trace_id branches
# ===========================================================================


@pytest.mark.unit
class TestShouldEmitEdgeCases:
    def _make_event(self, status: str = "ok", trace_id: str = "") -> Event:
        payload: dict = {"status": status}
        if trace_id:
            payload["trace_id"] = trace_id
        return Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="svc@0.0.0",
            payload=payload,
        )

    def test_invalid_hex_trace_id_uses_bucket_zero(self) -> None:
        """trace_id first 8 chars are non-hex → int() raises ValueError → bucket=0."""
        cfg = AgentOBSConfig(sample_rate=0.5, always_sample_errors=False)
        # "zzzzzzzz" is not valid hex — triggers the ValueError path → bucket 0
        # bucket 0 / 0xFFFFFFFF ≈ 0 < 0.5 → should emit
        event = self._make_event(trace_id="zzzzzzzz" + "0" * 24)
        assert _should_emit(event, cfg) is True

    def test_no_trace_id_uses_random(self) -> None:
        """Event with no trace_id falls back to random.random() sampling."""
        cfg = AgentOBSConfig(sample_rate=0.0, always_sample_errors=False)
        # At sample_rate=0.0 all random values > 0.0, so should drop
        event = self._make_event(trace_id="")
        assert _should_emit(event, cfg) is False

    def test_no_trace_id_sample_rate_one(self) -> None:
        """sample_rate=1.0 with trace_filters=[] hits the fast path."""
        cfg = AgentOBSConfig(sample_rate=1.0, always_sample_errors=True)
        event = self._make_event(trace_id="")
        assert _should_emit(event, cfg) is True


# ===========================================================================
# 15. _stream.emit_span — trace collector exception swallowing
# ===========================================================================


@pytest.mark.unit
class TestEmitSpanCollectorExceptionSwallowing:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_bad_trace_collector_does_not_surface_error(self) -> None:
        """If the Trace collector's _record_span raises, emit_span continues."""
        bad_collector = MagicMock()
        bad_collector._record_span.side_effect = RuntimeError("collector broke")

        # Use a real AgentRunContext so trace_id / span_id are valid strings
        real_run = AgentRunContext(agent_name="test-agent")
        real_run._trace_collector = bad_collector  # type: ignore[attr-defined]

        # Push the real run onto the run stack so the collector notification fires
        _run_stack_var.set((real_run,))
        try:
            with tracer.span("llm"):
                pass  # emit_span is called on __exit__
        finally:
            _run_stack_var.set(())
        # If we reach here the exception was swallowed — test passes


# ===========================================================================
# 16. Exception paths in context managers (__exit__ error-handler)
# ===========================================================================


@pytest.mark.unit
class TestContextManagerExitErrorHandling:
    def setup_method(self) -> None:
        _clean_stacks()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_span_exit_handles_export_error(self) -> None:
        """When emit_span raises, __exit__ routes it through _handle_export_error."""
        configure(on_export_error="drop")
        _reset_exporter()
        bad_exporter = MagicMock()
        bad_exporter.export.side_effect = RuntimeError("export blew up")
        stream_mod._cached_exporter = bad_exporter
        # Should not propagate the export error
        with tracer.span("llm"):
            ...
        configure(on_export_error="warn")

    def test_agent_step_exit_handles_export_error(self) -> None:
        configure(on_export_error="drop")
        _reset_exporter()
        bad_exporter = MagicMock()
        bad_exporter.export.side_effect = RuntimeError("step export blew up")
        stream_mod._cached_exporter = bad_exporter
        with tracer.agent_run("run"):
            with tracer.agent_step("step"):
                ...
        configure(on_export_error="warn")

    def test_agent_run_exit_handles_export_error(self) -> None:
        configure(on_export_error="drop")
        _reset_exporter()
        bad_exporter = MagicMock()
        bad_exporter.export.side_effect = RuntimeError("run export blew up")
        stream_mod._cached_exporter = bad_exporter
        with tracer.agent_run("run"):
            ...
        configure(on_export_error="warn")


# ===========================================================================
# 17. Full end-to-end integration
# ===========================================================================


@pytest.mark.integration
class TestPhase4EndToEnd:
    def setup_method(self) -> None:
        _clean_stacks()
        self.cap = _install_exporter()

    def teardown_method(self) -> None:
        _reset_exporter()
        _clean_stacks()

    def test_research_agent_scenario(self) -> None:
        """Full research agent: run + 2 search steps + summarize step."""
        with tracer.agent_run("research-agent") as run:
            with tracer.agent_step("web-search") as s1:
                s1.set_attribute("query", "what is RAG?")
                s1.token_usage = _token_usage(50)
                s1.cost = _cost_breakdown(0.005)
                s1.tool_calls.append(ToolCall(tool_call_id="tc-1", function_name="browser", status="success"))
            with tracer.agent_step("web-search") as s2:
                s2.set_attribute("query", "RAG examples")
                s2.token_usage = _token_usage(30)
                s2.cost = _cost_breakdown(0.003)
            with tracer.agent_step("summarize") as s3:
                s3.token_usage = _token_usage(100)
                s3.cost = _cost_breakdown(0.010)

        assert len(self.cap.events) == 4  # 3 steps + 1 run

        step_payloads = [
            e.payload for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_STEP
        ]
        assert [p["step_index"] for p in step_payloads] == [0, 1, 2]

        run_payload = next(
            e.payload for e in self.cap.events if e.event_type == EventType.TRACE_AGENT_COMPLETED
        )
        assert run_payload["total_steps"] == 3
        assert run_payload["total_model_calls"] == 3
        assert run_payload["total_tool_calls"] == 1
        assert run_payload["total_token_usage"]["total_tokens"] == (50 + 30 + 100) * 3
        assert abs(run_payload["total_cost"]["total_cost_usd"] - 0.018) < 1e-9

    def test_span_and_agent_interleaved(self) -> None:
        """Spans within agent steps share trace_id."""
        with tracer.agent_run("mixed") as run:
            with tracer.span("pre-llm"):
                ...
            with tracer.agent_step("step") as step:
                with tracer.span("inner-llm"):
                    ...
        trace_ids = {e.payload.get("trace_id") for e in self.cap.events}
        trace_ids.discard(None)
        assert len(trace_ids) == 1
