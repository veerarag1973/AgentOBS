"""Tests for agentobs._span — Span, SpanContextManager, AgentRun/Step contexts.

Phase 2 + 4 SDK coverage target.
"""

from __future__ import annotations

import pytest

from agentobs._span import (
    AgentRunContext,
    AgentRunContextManager,
    AgentStepContext,
    AgentStepContextManager,
    Span,
    SpanContextManager,
    _resolve_model_info,
    _run_stack,
    _run_stack_var,
    _span_id,
    _span_stack,
    _span_stack_var,
    _trace_id,
)
from agentobs.namespaces.trace import (
    CostBreakdown,
    GenAISystem,
    TokenUsage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_span(**kw) -> Span:
    defaults = {"name": "test-span"}
    defaults.update(kw)
    return Span(**defaults)


def _token_usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30)


def _cost() -> CostBreakdown:
    return CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.002, total_cost_usd=0.003)


# ===========================================================================
# ID generation
# ===========================================================================


@pytest.mark.unit
class TestIdGeneration:
    def test_span_id_is_16_hex(self) -> None:
        sid = _span_id()
        assert len(sid) == 16
        int(sid, 16)  # must be valid hex

    def test_trace_id_is_32_hex(self) -> None:
        tid = _trace_id()
        assert len(tid) == 32
        int(tid, 16)

    def test_span_id_unique_each_call(self) -> None:
        ids = {_span_id() for _ in range(100)}
        assert len(ids) == 100

    def test_trace_id_unique_each_call(self) -> None:
        ids = {_trace_id() for _ in range(100)}
        assert len(ids) == 100


# ===========================================================================
# Span creation and mutation
# ===========================================================================


@pytest.mark.unit
class TestSpan:
    def test_default_status_ok(self) -> None:
        span = _make_span()
        assert span.status == "ok"

    def test_default_attributes_empty(self) -> None:
        span = _make_span()
        assert span.attributes == {}

    def test_default_tool_calls_empty(self) -> None:
        span = _make_span()
        assert span.tool_calls == []

    def test_set_attribute_stores_value(self) -> None:
        span = _make_span()
        span.set_attribute("temperature", 0.7)
        assert span.attributes["temperature"] == 0.7

    def test_set_attribute_overrides_existing(self) -> None:
        span = _make_span()
        span.set_attribute("key", "v1")
        span.set_attribute("key", "v2")
        assert span.attributes["key"] == "v2"

    def test_set_attribute_empty_key_raises(self) -> None:
        span = _make_span()
        with pytest.raises(ValueError, match="non-empty string"):
            span.set_attribute("", "value")

    def test_set_attribute_non_string_key_raises(self) -> None:
        span = _make_span()
        with pytest.raises(ValueError, match="non-empty string"):
            span.set_attribute(123, "value")  # type: ignore[arg-type]

    def test_record_error_sets_status(self) -> None:
        span = _make_span()
        span.record_error(ValueError("oops"))
        assert span.status == "error"
        assert "oops" in span.error  # type: ignore[operator]
        assert span.error_type == "ValueError"

    def test_record_error_nested_exception(self) -> None:
        span = _make_span()
        try:
            raise RuntimeError("something broke")  # noqa: TRY301
        except RuntimeError as exc:
            span.record_error(exc)
        assert span.status == "error"
        assert span.error_type == "RuntimeError"

    def test_end_sets_duration_ms(self) -> None:
        span = _make_span()
        span.end()
        assert span.end_ns is not None
        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_end_idempotent(self) -> None:
        span = _make_span()
        span.end()
        first_end = span.end_ns
        span.end()
        assert span.end_ns == first_end  # not reset on second call

    def test_set_token_usage(self) -> None:
        span = _make_span()
        tu = _token_usage()
        span.set_token_usage(tu)
        assert span.token_usage is tu

    def test_set_cost(self) -> None:
        span = _make_span()
        cb = _cost()
        span.set_cost(cb)
        assert span.cost is cb

    def test_to_span_payload_required_fields(self) -> None:
        span = _make_span()
        span.end()
        payload = span.to_span_payload()
        assert payload.span_name == "test-span"
        assert payload.status == "ok"
        assert payload.span_id == span.span_id
        assert payload.trace_id == span.trace_id

    def test_to_span_payload_with_model(self) -> None:
        span = _make_span(model="gpt-4o")
        span.end()
        payload = span.to_span_payload()
        assert payload.model is not None
        assert payload.model.name == "gpt-4o"

    def test_to_span_payload_with_error(self) -> None:
        span = _make_span()
        span.record_error(TimeoutError("too slow"))
        span.end()
        payload = span.to_span_payload()
        assert payload.status == "error"
        assert payload.error == "too slow"

    def test_to_span_payload_empty_attributes_excluded(self) -> None:
        span = _make_span()
        span.end()
        payload = span.to_span_payload()
        assert payload.attributes is None

    def test_to_span_payload_attributes_included_when_present(self) -> None:
        span = _make_span()
        span.set_attribute("foo", "bar")
        span.end()
        payload = span.to_span_payload()
        assert payload.attributes == {"foo": "bar"}

    def test_to_span_payload_custom_operation(self) -> None:
        span = _make_span(operation="embedding")
        span.end()
        payload = span.to_span_payload()
        # operation is stored / returned as a plain string
        op = payload.operation
        assert (op.value if hasattr(op, "value") else op) == "embedding"

    def test_to_span_payload_unknown_operation(self) -> None:
        span = _make_span(operation="my_custom_op")
        span.end()
        payload = span.to_span_payload()
        assert payload.operation == "my_custom_op"


# ===========================================================================
# SpanContextManager
# ===========================================================================


@pytest.mark.unit
class TestSpanContextManager:
    def test_yields_span_on_enter(self) -> None:
        with SpanContextManager(name="test") as span:
            assert isinstance(span, Span)
            assert span.name == "test"

    def test_span_stack_populated_during_context(self) -> None:
        with SpanContextManager(name="inner") as span:
            stack = _span_stack()
            assert span in stack

    def test_span_stack_cleared_after_context(self) -> None:
        with SpanContextManager(name="outer"):
            pass
        # span should be removed from stack
        stack = _span_stack()
        # can't guarantee empty (other concurrent tests) but our span is gone
        for s in stack:
            assert s.name != "outer"

    def test_nested_spans_inherit_trace_id(self) -> None:
        with SpanContextManager(name="parent") as parent:  # noqa: SIM117
            with SpanContextManager(name="child") as child:
                assert child.trace_id == parent.trace_id

    def test_nested_spans_parent_id_set(self) -> None:
        with SpanContextManager(name="parent") as parent:  # noqa: SIM117
            with SpanContextManager(name="child") as child:
                assert child.parent_span_id == parent.span_id

    def test_root_span_has_no_parent(self) -> None:
        # Ensure stack is clear for this test
        _span_stack_var.set(())
        with SpanContextManager(name="root") as span:
            assert span.parent_span_id is None

    def test_exception_records_error_on_span(self) -> None:
        caught = None
        try:
            with SpanContextManager(name="failing") as span:
                raise ValueError("deliberate error")  # noqa: TRY301
        except ValueError as exc:
            caught = exc
        assert caught is not None
        assert span.status == "error"
        assert "deliberate error" in (span.error or "")

    def test_exception_propagates_from_span_context(self) -> None:
        with pytest.raises(RuntimeError, match="propagated"):  # noqa: SIM117
            with SpanContextManager(name="err-span"):
                raise RuntimeError("propagated")

    def test_model_attribute_set(self) -> None:
        with SpanContextManager(name="model-span", model="claude-3-opus") as span:
            assert span.model == "claude-3-opus"

    def test_initial_attributes_applied(self) -> None:
        with SpanContextManager(name="attr-span", attributes={"key": "val"}) as span:
            assert span.attributes["key"] == "val"

    def test_span_ended_after_exit(self) -> None:
        with SpanContextManager(name="timed") as span:
            pass
        assert span.duration_ms is not None
        assert span.duration_ms >= 0


# ===========================================================================
# AgentRunContextManager
# ===========================================================================


@pytest.mark.unit
class TestAgentRunContextManager:
    def setup_method(self) -> None:
        _run_stack_var.set(())

    def test_yields_agent_run_context(self) -> None:
        with AgentRunContextManager("my-agent") as run:
            assert isinstance(run, AgentRunContext)
            assert run.agent_name == "my-agent"

    def test_populates_run_stack(self) -> None:
        with AgentRunContextManager("agent") as run:
            stack = _run_stack()
            assert run in stack

    def test_clears_run_stack_on_exit(self) -> None:
        with AgentRunContextManager("agent"):
            pass
        assert len(_run_stack()) == 0

    def test_run_has_trace_id(self) -> None:
        with AgentRunContextManager("agent") as run:
            assert len(run.trace_id) == 32

    def test_exception_sets_error_status(self) -> None:
        ctx = None
        try:
            with AgentRunContextManager("agent") as run:
                ctx = run
                raise RuntimeError("agent failed")  # noqa: TRY301
        except RuntimeError:
            pass
        assert ctx is not None
        assert ctx.status == "error"

    def test_exception_propagates(self) -> None:
        with pytest.raises(KeyError), AgentRunContextManager("bad-agent"):
            raise KeyError("missing")


# ===========================================================================
# AgentStepContextManager
# ===========================================================================


@pytest.mark.unit
class TestAgentStepContextManager:
    def setup_method(self) -> None:
        _run_stack_var.set(())
        _span_stack_var.set(())

    def test_step_outside_run_raises(self) -> None:
        with pytest.raises(RuntimeError, match="tracer.agent_step\\(\\)"):  # noqa: SIM117
            with AgentStepContextManager("step"):
                pass

    def test_step_inside_run_yields_context(self) -> None:
        with AgentRunContextManager("agent"), AgentStepContextManager("step-1") as step:
            assert isinstance(step, AgentStepContext)
            assert step.step_name == "step-1"

    def test_step_index_increments(self) -> None:
        with AgentRunContextManager("agent"):
            with AgentStepContextManager("step-0") as s0:
                idx0 = s0.step_index
            with AgentStepContextManager("step-1") as s1:
                idx1 = s1.step_index
        assert idx0 == 0
        assert idx1 == 1

    def test_step_inherits_trace_id_from_run(self) -> None:
        with AgentRunContextManager("agent") as run:  # noqa: SIM117
            with AgentStepContextManager("step") as step:
                assert step.trace_id == run.trace_id

    def test_step_records_in_run(self) -> None:
        with AgentRunContextManager("agent") as run:
            with AgentStepContextManager("step-A"):
                pass
            with AgentStepContextManager("step-B"):
                pass
        assert len(run._steps) == 2

    def test_step_exception_sets_error_status(self) -> None:
        ctx = None
        try:
            with AgentRunContextManager("agent"):  # noqa: SIM117
                with AgentStepContextManager("bad-step") as step:
                    ctx = step
                    raise ValueError("step failed")  # noqa: TRY301
        except ValueError:
            pass
        assert ctx is not None
        assert ctx.status == "error"

    def test_step_attribute_setting(self) -> None:
        with AgentRunContextManager("agent"), AgentStepContextManager("step") as step:
            step.set_attribute("tool", "web-search")
        assert step.attributes["tool"] == "web-search"

    def test_step_duration_set_on_exit(self) -> None:
        with AgentRunContextManager("agent"), AgentStepContextManager("step") as step:
            pass
        assert step.duration_ms is not None
        assert step.duration_ms >= 0


# ===========================================================================
# AgentRunContext.to_agent_run_payload
# ===========================================================================


@pytest.mark.unit
class TestAgentRunContextToPayload:
    def setup_method(self) -> None:
        _run_stack_var.set(())

    def test_run_payload_aggregates_steps(self) -> None:
        with AgentRunContextManager("agent") as run:
            with AgentStepContextManager("s1") as step:
                # token_usage is a direct attribute, not a method
                step.token_usage = _token_usage()
            with AgentStepContextManager("s2"):
                pass
        payload = run.to_agent_run_payload()
        assert payload.total_steps == 2

    def test_run_payload_aggregates_cost(self) -> None:
        with AgentRunContextManager("agent") as run, AgentStepContextManager("s") as step:
            step.cost = _cost()
        payload = run.to_agent_run_payload()
        assert payload.total_cost.total_cost_usd == pytest.approx(0.003)

    def test_run_payload_empty_steps(self) -> None:
        with AgentRunContextManager("agent") as run:
            pass
        payload = run.to_agent_run_payload()
        assert payload.total_steps == 0
        assert payload.total_model_calls == 0


# ===========================================================================
# _resolve_model_info heuristics
# ===========================================================================


@pytest.mark.unit
class TestResolveModelInfo:
    def test_gpt_resolves_openai(self) -> None:
        info = _resolve_model_info("gpt-4o")
        assert info.system == GenAISystem.OPENAI
        assert info.name == "gpt-4o"

    def test_claude_resolves_anthropic(self) -> None:
        info = _resolve_model_info("claude-3-opus")
        assert info.system == GenAISystem.ANTHROPIC

    def test_gemini_resolves_vertex(self) -> None:
        info = _resolve_model_info("gemini-1.5-pro")
        assert info.system == GenAISystem.VERTEX_AI

    def test_command_resolves_cohere(self) -> None:
        info = _resolve_model_info("command-r")
        assert info.system == GenAISystem.COHERE

    def test_mistral_resolves_mistral_ai(self) -> None:
        info = _resolve_model_info("mistral-large")
        assert info.system == GenAISystem.MISTRAL_AI

    def test_mixtral_resolves_mistral_ai(self) -> None:
        info = _resolve_model_info("mixtral-8x7b")
        assert info.system == GenAISystem.MISTRAL_AI

    def test_llama_resolves_ollama(self) -> None:
        info = _resolve_model_info("llama-3-8b")
        assert info.system == GenAISystem.OLLAMA

    def test_phi_resolves_ollama(self) -> None:
        info = _resolve_model_info("phi-3-mini")
        assert info.system == GenAISystem.OLLAMA

    def test_qwen_resolves_ollama(self) -> None:
        info = _resolve_model_info("qwen-72b")
        assert info.system == GenAISystem.OLLAMA

    def test_unknown_model_defaults_to_openai(self) -> None:
        info = _resolve_model_info("some-unknown-model")
        assert info.system == GenAISystem.OPENAI
