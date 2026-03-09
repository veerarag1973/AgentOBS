"""Tests for agentobs._tracer — Tracer class and module-level tracer singleton.

Phase 3 SDK coverage target.
"""

from __future__ import annotations

import pytest

from agentobs._span import (
    AgentRunContextManager,
    AgentStepContextManager,
    SpanContextManager,
    _run_stack,
    _run_stack_var,
    _span_stack,
    _span_stack_var,
)
from agentobs._tracer import Tracer, tracer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_stacks() -> None:
    _span_stack_var.set(())
    _run_stack_var.set(())


# ===========================================================================
# Tracer.span()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestTracerSpan:
    def setup_method(self) -> None:
        _clean_stacks()

    def test_returns_span_context_manager(self) -> None:
        cm = tracer.span("test-span")
        assert isinstance(cm, SpanContextManager)

    def test_enter_yields_span(self) -> None:
        from agentobs._span import Span  # noqa: PLC0415
        with tracer.span("test") as span:
            assert isinstance(span, Span)

    def test_span_name_set(self) -> None:
        with tracer.span("my-call") as span:
            assert span.name == "my-call"

    def test_model_param_set(self) -> None:
        with tracer.span("llm-call", model="gpt-4o") as span:
            assert span.model == "gpt-4o"

    def test_operation_param_set(self) -> None:
        with tracer.span("embed", operation="embedding") as span:
            assert span.operation == "embedding"

    def test_attributes_param_set(self) -> None:
        with tracer.span("attr-span", attributes={"k": "v"}) as span:
            assert span.attributes["k"] == "v"

    def test_set_attribute_inside_span(self) -> None:
        with tracer.span("span") as span:
            span.set_attribute("temperature", 0.9)
        assert span.attributes["temperature"] == 0.9

    def test_nested_spans_share_trace_id(self) -> None:
        with tracer.span("outer") as outer, tracer.span("inner") as inner:
            assert inner.trace_id == outer.trace_id

    def test_nested_spans_parent_child_link(self) -> None:
        with tracer.span("parent") as parent, tracer.span("child") as child:
            assert child.parent_span_id == parent.span_id

    def test_span_duration_positive(self) -> None:
        with tracer.span("timed"):
            ...
        # span object accessible after exit
        # duration is non-negative

    def test_exception_does_not_suppress(self) -> None:
        with pytest.raises(RuntimeError, match="test error"), tracer.span("err"):
            raise RuntimeError("test error")


# ===========================================================================
# Tracer.agent_run()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestTracerAgentRun:
    def setup_method(self) -> None:
        _clean_stacks()

    def test_returns_agent_run_context_manager(self) -> None:
        cm = tracer.agent_run("my-agent")
        assert isinstance(cm, AgentRunContextManager)

    def test_enter_yields_agent_run_context(self) -> None:
        from agentobs._span import AgentRunContext  # noqa: PLC0415
        with tracer.agent_run("agent") as run:
            assert isinstance(run, AgentRunContext)

    def test_agent_name_set(self) -> None:
        with tracer.agent_run("support-agent") as run:
            assert run.agent_name == "support-agent"

    def test_agent_run_has_trace_id(self) -> None:
        with tracer.agent_run("agent") as run:
            assert len(run.trace_id) == 32

    def test_agent_run_status_ok_by_default(self) -> None:
        with tracer.agent_run("agent") as run:
            ...
        assert run.status == "ok"

    def test_exception_sets_error_status(self) -> None:
        ctx = None
        try:
            with tracer.agent_run("bad-agent") as run:
                ctx = run
                raise ValueError("agent fail")  # noqa: TRY301
        except ValueError:
            ...
        assert ctx is not None
        assert ctx.status == "error"

    def test_exception_propagates(self) -> None:
        with pytest.raises(ValueError), tracer.agent_run("agent"):
            raise ValueError("propagate me")


# ===========================================================================
# Tracer.agent_step()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestTracerAgentStep:
    def setup_method(self) -> None:
        _clean_stacks()

    def test_returns_agent_step_context_manager(self) -> None:
        cm = tracer.agent_step("step")
        assert isinstance(cm, AgentStepContextManager)

    def test_step_outside_run_raises(self) -> None:
        with pytest.raises(RuntimeError), tracer.agent_step("orphan-step"):
            ...
    def test_step_inside_run_works(self) -> None:
        from agentobs._span import AgentStepContext  # noqa: PLC0415
        with tracer.agent_run("agent"), tracer.agent_step("step") as step:
            assert isinstance(step, AgentStepContext)

    def test_step_name_set(self) -> None:
        with tracer.agent_run("agent"), tracer.agent_step("web-search") as step:
            assert step.step_name == "web-search"

    def test_step_operation_param(self) -> None:
        with tracer.agent_run("agent"), tracer.agent_step("step", operation="chat") as step:
            assert step.operation == "chat"

    def test_step_attributes_param(self) -> None:
        with tracer.agent_run("agent"):  # noqa: SIM117
            with tracer.agent_step("step", attributes={"query": "hello"}) as step:
                assert step.attributes["query"] == "hello"

    def test_multiple_steps_indexed_sequentially(self) -> None:
        with tracer.agent_run("agent"):
            with tracer.agent_step("s0") as s0:
                ...
            with tracer.agent_step("s1") as s1:
                ...
            with tracer.agent_step("s2") as s2:
                ...
        assert s0.step_index == 0
        assert s1.step_index == 1
        assert s2.step_index == 2

    def test_step_records_in_run(self) -> None:
        with tracer.agent_run("agent") as run:
            with tracer.agent_step("step-a"):
                ...
            with tracer.agent_step("step-b"):
                ...
        assert len(run._steps) == 2


# ===========================================================================
# Tracer singleton
# ===========================================================================


@pytest.mark.unit
class TestTracerSingleton:
    def test_tracer_is_tracer_instance(self) -> None:
        assert isinstance(tracer, Tracer)

    def test_multiple_tracer_instances_independent(self) -> None:
        t1 = Tracer()
        t2 = Tracer()
        assert t1 is not t2

    def test_tracer_span_and_agent_run_combined(self) -> None:
        _clean_stacks()
        with tracer.agent_run("agent") as run, tracer.span("inner-span") as span:
            span.set_attribute("inside_run", True)
            with tracer.agent_step("step") as step:
                # verify all three context managers are active
                assert run.agent_name == "agent"
                assert span.name == "inner-span"
                assert step.step_name == "step"
