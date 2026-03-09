"""tests/test_phase1_context_trace.py — Exhaustive tests for Phase 1 changes.

Phase 1 covers:
- contextvars-based span/run stacks replacing threading.local
- async context managers on SpanContextManager, AgentRunContextManager,
  AgentStepContextManager
- Trace class and start_trace() imperative API
- copy_context() helper
- Context isolation across concurrent asyncio tasks and threads

Coverage target: ≥ 95 % of agentobs/_span.py, agentobs/_trace.py,
and the Phase 1 additions to agentobs/_tracer.py.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import agentobs
from agentobs import (
    Trace,
    configure,
    copy_context,
    start_trace,
    tracer,
)
from agentobs._span import (
    AgentRunContext,
    AgentRunContextManager,
    AgentStepContext,
    AgentStepContextManager,
    Span,
    SpanContextManager,
    _run_stack_var,
    _span_stack_var,
    _span_id,
    _trace_id,
    _now_ns,
)
from agentobs._trace import Trace as TraceClass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_export(monkeypatch):
    """Suppress all export side-effects for every test."""
    monkeypatch.setattr("agentobs._stream._dispatch", lambda event: None)
    yield


@pytest.fixture()
def tmp_jsonl(tmp_path):
    return tmp_path / "trace.jsonl"


# ===========================================================================
# 1. ID generation helpers
# ===========================================================================


class TestIDHelpers:
    def test_span_id_length(self):
        sid = _span_id()
        assert len(sid) == 16
        assert all(c in "0123456789abcdef" for c in sid)

    def test_trace_id_length(self):
        tid = _trace_id()
        assert len(tid) == 32
        assert all(c in "0123456789abcdef" for c in tid)

    def test_ids_are_unique(self):
        ids = {_span_id() for _ in range(100)}
        assert len(ids) == 100

    def test_now_ns_is_nanoseconds(self):
        t = _now_ns()
        assert isinstance(t, int)
        # Should be within 1 second of time.time_ns()
        assert abs(t - time.time_ns()) < 1_000_000_000


# ===========================================================================
# 2. ContextVar stack isolation
# ===========================================================================


class TestContextVarStacks:
    def test_initial_stack_is_empty(self):
        assert _span_stack_var.get() == ()
        assert _run_stack_var.get() == ()

    def test_span_stack_pushes_and_pops(self):
        assert _span_stack_var.get() == ()
        with tracer.span("outer") as outer:
            assert _span_stack_var.get() == (outer,)
            with tracer.span("inner") as inner:
                assert _span_stack_var.get() == (outer, inner)
            assert _span_stack_var.get() == (outer,)
        assert _span_stack_var.get() == ()

    def test_run_stack_pushes_and_pops(self):
        assert _run_stack_var.get() == ()
        with tracer.agent_run("agent") as run:
            assert len(_run_stack_var.get()) == 1
            assert _run_stack_var.get()[0] is run
        assert _run_stack_var.get() == ()

    def test_stack_restored_on_exception(self):
        with pytest.raises(ValueError):
            with tracer.span("boom"):
                raise ValueError("oops")
        assert _span_stack_var.get() == ()

    def test_run_stack_restored_on_exception(self):
        with pytest.raises(RuntimeError):
            with tracer.agent_run("agent"):
                raise RuntimeError("bad")
        assert _run_stack_var.get() == ()

    def test_stacks_independent_across_threads(self):
        """Each thread must have its own empty stack."""
        errors: list[str] = []

        def thread_check():
            if _span_stack_var.get() != ():
                errors.append("span_stack not empty in new thread")
            if _run_stack_var.get() != ():
                errors.append("run_stack not empty in new thread")

        t = threading.Thread(target=thread_check)
        t.start()
        t.join()
        assert errors == []

    def test_stack_not_shared_between_concurrent_threads(self):
        """Spans opened in one thread must not appear in another."""
        thread_saw_span: list[bool] = [False]
        main_span_entered = threading.Event()
        thread_checked = threading.Event()

        def check_thread():
            main_span_entered.wait()
            thread_saw_span[0] = len(_span_stack_var.get()) > 0
            thread_checked.set()

        t = threading.Thread(target=check_thread)
        t.start()

        with tracer.span("main-thread-span"):
            main_span_entered.set()
            thread_checked.wait()

        t.join()
        assert thread_saw_span[0] is False


# ===========================================================================
# 3. Asyncio context isolation
# ===========================================================================


class TestAsyncContextIsolation:
    def test_async_span_stack_isolated_between_tasks(self):
        """Two concurrent asyncio tasks must not see each other's spans."""

        async def task_a(results: list):
            async with tracer.span("a-outer"):
                await asyncio.sleep(0)  # yield to let task_b run
                stack = _span_stack_var.get()
                # task_a should only see its own spans
                results.append(("a", [s.name for s in stack]))

        async def task_b(results: list):
            async with tracer.span("b-outer"):
                await asyncio.sleep(0)
                stack = _span_stack_var.get()
                results.append(("b", [s.name for s in stack]))

        async def main():
            results: list = []
            await asyncio.gather(task_a(results), task_b(results))
            return results

        results = asyncio.run(main())
        a_names = next(r[1] for r in results if r[0] == "a")
        b_names = next(r[1] for r in results if r[0] == "b")
        assert a_names == ["a-outer"], f"task a saw: {a_names}"
        assert b_names == ["b-outer"], f"task b saw: {b_names}"

    def test_nested_async_spans_inherit_trace_id(self):
        async def main():
            async with tracer.span("root") as root:
                async with tracer.span("child") as child:
                    return root.trace_id, child.trace_id, child.parent_span_id

        root_tid, child_tid, parent_sid = asyncio.run(main())
        assert root_tid == child_tid
        assert parent_sid is not None

    def test_async_task_inherits_parent_span_on_spawning(self):
        """asyncio.create_task copies context — child task can see parent span."""

        async def main():
            outer_tid = None
            inner_tid = None

            async with tracer.span("outer") as outer:
                outer_tid = outer.trace_id

                async def inner_task():
                    nonlocal inner_tid
                    await asyncio.sleep(0)  # yield control to satisfy async requirement
                    stack = _span_stack_var.get()
                    # The task was created inside the outer span, so it
                    # *copies* the context at task-creation time.
                    if stack:
                        inner_tid = stack[-1].trace_id

                task = asyncio.create_task(inner_task())
                await task

            return outer_tid, inner_tid

        outer_tid, inner_tid = asyncio.run(main())
        assert outer_tid is not None
        # The spawned task inherits the parent context snapshot
        assert inner_tid == outer_tid

    def test_async_stack_clean_after_async_with(self):
        async def main():
            async with tracer.span("x"):
                ...
            return _span_stack_var.get()

        result = asyncio.run(main())
        assert result == ()

    def test_async_run_stack_restored_on_exception(self):
        async def main():
            with pytest.raises(ValueError):
                async with tracer.agent_run("bad-agent"):
                    raise ValueError("crash")
            return _run_stack_var.get()

        result = asyncio.run(main())
        assert result == ()

    def test_concurrent_agent_runs_isolated(self):
        """Two concurrent agent runs must not bleed into each other."""

        async def run_agent(name: str, results: dict):
            async with tracer.agent_run(name):
                await asyncio.sleep(0)
                results[name] = _run_stack_var.get()[-1].agent_name

        async def main():
            results: dict = {}
            await asyncio.gather(
                run_agent("agent-a", results),
                run_agent("agent-b", results),
            )
            return results

        results = asyncio.run(main())
        assert results["agent-a"] == "agent-a"
        assert results["agent-b"] == "agent-b"


# ===========================================================================
# 4. copy_context() helper
# ===========================================================================


class TestCopyContext:
    def test_copy_context_returns_context_type(self):
        ctx = copy_context()
        assert isinstance(ctx, contextvars.Context)

    def test_copy_context_propagates_span_to_thread(self):
        """Thread launched via copy_context().run sees the active span."""
        captured_trace_id: list[str] = []

        with tracer.span("outer") as outer:
            ctx = copy_context()

            def thread_fn():
                stack = _span_stack_var.get()
                if stack:
                    captured_trace_id.append(stack[-1].trace_id)

            t = threading.Thread(target=lambda: ctx.run(thread_fn))
            t.start()
            t.join()

        assert captured_trace_id == [outer.trace_id]

    def test_copy_context_thread_mutation_does_not_affect_main(self):
        """Mutations in the thread context must not affect the main context."""

        def thread_fn():
            # Open a new span inside the thread — this creates a modified ContextVar
            # but only within the thread's copy of the context.
            with tracer.span("thread-span"):
                ...
        with tracer.span("main-span") as main_span:
            ctx = copy_context()
            t = threading.Thread(target=lambda: ctx.run(thread_fn))
            t.start()
            t.join()
            # Main context should still only have main-span
            assert _span_stack_var.get() == (main_span,)


# ===========================================================================
# 5. SpanContextManager — full protocol
# ===========================================================================


class TestSpanContextManager:
    def test_returns_span_on_enter(self):
        with tracer.span("test") as s:
            assert isinstance(s, Span)

    def test_span_has_correct_name(self):
        with tracer.span("my-span") as s:
            assert s.name == "my-span"

    def test_span_has_model(self):
        with tracer.span("s", model="gpt-4o") as s:
            assert s.model == "gpt-4o"

    def test_span_has_operation(self):
        with tracer.span("s", operation="embeddings") as s:
            assert s.operation == "embeddings"

    def test_initial_attributes_copied(self):
        attrs = {"key": "value", "num": 42}
        with tracer.span("s", attributes=attrs) as s:
            assert s.attributes["key"] == "value"
            assert s.attributes["num"] == 42

    def test_initial_attributes_not_aliased(self):
        attrs = {"k": "v"}
        with tracer.span("s", attributes=attrs) as s:
            s.set_attribute("k", "changed")
        assert attrs["k"] == "v"  # original not mutated

    def test_root_span_generates_trace_id(self):
        with tracer.span("root") as s:
            assert len(s.trace_id) == 32

    def test_root_span_has_no_parent(self):
        with tracer.span("root") as s:
            assert s.parent_span_id is None

    def test_child_inherits_trace_id(self):
        with tracer.span("parent") as p:
            with tracer.span("child") as c:
                assert c.trace_id == p.trace_id

    def test_child_has_parent_span_id(self):
        with tracer.span("parent") as p:
            with tracer.span("child") as c:
                assert c.parent_span_id == p.span_id

    def test_three_level_nesting(self):
        with tracer.span("a") as a:
            with tracer.span("b") as b:
                with tracer.span("c") as c:
                    assert c.trace_id == a.trace_id
                    assert c.parent_span_id == b.span_id

    def test_duration_set_on_exit(self):
        with tracer.span("t") as s:
            ...
        assert s.duration_ms is not None
        assert s.duration_ms >= 0

    def test_status_ok_by_default(self):
        with tracer.span("t") as s:
            ...
        assert s.status == "ok"

    def test_exception_sets_error_status(self):
        with pytest.raises(ValueError):
            with tracer.span("t") as s:
                raise ValueError("boom")
        assert s.status == "error"
        assert "boom" in s.error

    def test_exception_not_suppressed(self):
        with pytest.raises(RuntimeError):
            with tracer.span("t"):
                raise RuntimeError("should propagate")

    def test_pre_set_error_not_overwritten(self):
        """If caller already set status=error before an exception, keep original."""
        with pytest.raises(ValueError):
            with tracer.span("t") as s:
                s.status = "error"
                s.error = "manual error"
                raise ValueError("second error")
        assert s.error == "manual error"

    def test_span_end_idempotent(self):
        with tracer.span("t") as s:
            ...
        first_end = s.end_ns
        s.end()
        assert s.end_ns == first_end  # not re-set

    def test_set_attribute_valid(self):
        with tracer.span("t") as s:
            s.set_attribute("foo", "bar")
        assert s.attributes["foo"] == "bar"

    def test_set_attribute_empty_key_raises(self):
        with tracer.span("t") as s:
            with pytest.raises(ValueError, match="non-empty string"):
                s.set_attribute("", "val")

    def test_set_attribute_non_string_key_raises(self):
        with tracer.span("t") as s:
            with pytest.raises(ValueError):
                s.set_attribute(123, "val")  # type: ignore[arg-type]

    def test_record_error(self):
        with tracer.span("t") as s:
            s.record_error(RuntimeError("oops"))
        assert s.status == "error"
        assert s.error == "oops"
        assert "RuntimeError" in s.error_type

    def test_to_span_payload_roundtrip(self):
        with tracer.span("roundtrip", model="gpt-4o") as s:
            s.set_attribute("x", 1)
        payload = s.to_span_payload()
        d = payload.to_dict()
        assert d["span_name"] == "roundtrip"
        assert d["status"] == "ok"
        assert d["attributes"]["x"] == 1

    def test_two_sequential_spans_different_trace_ids(self):
        with tracer.span("first") as a:
            tid_a = a.trace_id
        with tracer.span("second") as b:
            tid_b = b.trace_id
        assert tid_a != tid_b

    # ------------------------------------------------------------------
    # Async protocol
    # ------------------------------------------------------------------

    def test_async_enter_exit(self):
        async def main():
            async with tracer.span("async-span") as s:
                return s

        s = asyncio.run(main())
        assert isinstance(s, Span)
        assert s.status == "ok"

    def test_async_exception_captured(self):
        async def main():
            with pytest.raises(ValueError):
                async with tracer.span("async-err") as s:
                    raise ValueError("async boom")
            return s

        s = asyncio.run(main())
        assert s.status == "error"

    def test_sync_and_async_interop(self):
        """Nested sync inside async works; trace_id inherited."""

        async def main():
            async with tracer.span("async-outer") as outer:
                with tracer.span("sync-inner") as inner:
                    return outer.trace_id, inner.trace_id, inner.parent_span_id

        outer_tid, inner_tid, parent_sid = asyncio.run(main())
        assert outer_tid == inner_tid
        assert parent_sid is not None


# ===========================================================================
# 6. AgentRunContextManager — full protocol
# ===========================================================================


class TestAgentRunContextManager:
    def test_returns_agent_run_context(self):
        with tracer.agent_run("agent") as run:
            assert isinstance(run, AgentRunContext)

    def test_agent_name_set(self):
        with tracer.agent_run("my-agent") as run:
            assert run.agent_name == "my-agent"

    def test_trace_id_is_32_hex(self):
        with tracer.agent_run("a") as run:
            assert len(run.trace_id) == 32

    def test_run_stack_has_run_context(self):
        with tracer.agent_run("a") as run:
            assert _run_stack_var.get()[-1] is run

    def test_run_stack_empty_after_exit(self):
        with tracer.agent_run("a"):
            ...
        assert _run_stack_var.get() == ()

    def test_exception_in_run_sets_error(self):
        with pytest.raises(ZeroDivisionError):
            with tracer.agent_run("a") as run:
                raise ZeroDivisionError("div0")
        assert run.status == "error"
        assert "div0" in run.error

    def test_next_step_index_increments(self):
        with tracer.agent_run("a") as run:
            assert run.next_step_index() == 0
            assert run.next_step_index() == 1
            assert run.next_step_index() == 2

    def test_async_agent_run(self):
        async def main():
            async with tracer.agent_run("async-agent") as run:
                return run.agent_name

        name = asyncio.run(main())
        assert name == "async-agent"

    def test_async_exception_in_run(self):
        async def main():
            with pytest.raises(RuntimeError):
                async with tracer.agent_run("a") as run:
                    raise RuntimeError("async run fail")
            return run

        run = asyncio.run(main())
        assert run.status == "error"

    def test_nested_runs_not_supported_but_stack_correct(self):
        """Nested agent runs push multiple entries on the run stack."""
        with tracer.agent_run("outer"):
            with tracer.agent_run("inner"):
                stack = _run_stack_var.get()
                assert len(stack) == 2
                assert stack[0].agent_name == "outer"
                assert stack[1].agent_name == "inner"
            assert len(_run_stack_var.get()) == 1
        assert _run_stack_var.get() == ()


# ===========================================================================
# 7. AgentStepContextManager — full protocol
# ===========================================================================


class TestAgentStepContextManager:
    def test_step_outside_run_raises(self):
        with pytest.raises(RuntimeError, match="agent_run"):
            with tracer.agent_step("step"):
                ...
    def test_returns_agent_step_context(self):
        with tracer.agent_run("a"):
            with tracer.agent_step("step") as ctx:
                assert isinstance(ctx, AgentStepContext)

    def test_step_inherits_agent_run_id(self):
        with tracer.agent_run("a") as run:
            with tracer.agent_step("step") as step:
                assert step.agent_run_id == run.agent_run_id

    def test_step_index_increments(self):
        with tracer.agent_run("a"):
            with tracer.agent_step("s1") as s1:
                ...
            with tracer.agent_step("s2") as s2:
                ...
        assert s1.step_index == 0
        assert s2.step_index == 1

    def test_step_inherits_trace_id_from_run(self):
        with tracer.agent_run("a") as run:
            with tracer.agent_step("s") as step:
                assert step.trace_id == run.trace_id

    def test_step_inherits_trace_id_from_enclosing_span(self):
        with tracer.agent_run("a"):
            with tracer.span("s") as span:
                with tracer.agent_step("sub") as step:
                    assert step.trace_id == span.trace_id

    def test_exception_in_step_sets_error(self):
        with tracer.agent_run("a"):
            with pytest.raises(KeyError):
                with tracer.agent_step("bad") as step:
                    raise KeyError("missing key")
        assert step.status == "error"

    def test_duration_set_on_step_exit(self):
        with tracer.agent_run("a"):
            with tracer.agent_step("t") as step:
                ...
        assert step.duration_ms is not None

    def test_step_recorded_on_run_context(self):
        with tracer.agent_run("a") as run:
            with tracer.agent_step("s"):
                ...
        assert len(run._steps) == 1

    def test_async_step(self):
        async def main():
            async with tracer.agent_run("a") as run:
                async with tracer.agent_step("s") as step:
                    return step.agent_run_id, run.agent_run_id

        step_rid, run_rid = asyncio.run(main())
        assert step_rid == run_rid

    def test_step_set_attribute(self):
        with tracer.agent_run("a"):
            with tracer.agent_step("s") as step:
                step.set_attribute("query", "hello world")
        assert step.attributes["query"] == "hello world"

    def test_step_set_attribute_empty_key_raises(self):
        with tracer.agent_run("a"):
            with tracer.agent_step("s") as step:
                with pytest.raises(ValueError):
                    step.set_attribute("", "v")


# ===========================================================================
# 8. Span.to_span_payload()
# ===========================================================================


class TestSpanToPayload:
    def test_required_fields_present(self):
        with tracer.span("p", model="claude-3-5") as s:
            ...
        p = s.to_span_payload()
        assert p.span_name == "p"
        assert p.span_id == s.span_id
        assert p.trace_id == s.trace_id
        assert p.model is not None
        assert p.model.name == "claude-3-5"

    def test_model_system_inferred_openai(self):
        with tracer.span("x", model="gpt-4o") as s:
            ...
        p = s.to_span_payload()
        from agentobs.namespaces.trace import GenAISystem
        assert p.model.system == GenAISystem.OPENAI

    def test_model_system_inferred_anthropic(self):
        with tracer.span("x", model="claude-3-haiku") as s:
            ...
        p = s.to_span_payload()
        from agentobs.namespaces.trace import GenAISystem
        assert p.model.system == GenAISystem.ANTHROPIC

    def test_model_system_inferred_mistral(self):
        with tracer.span("x", model="mistral-large") as s:
            ...
        p = s.to_span_payload()
        from agentobs.namespaces.trace import GenAISystem
        assert p.model.system == GenAISystem.MISTRAL_AI

    def test_model_system_inferred_ollama_llama(self):
        with tracer.span("x", model="llama-3") as s:
            ...
        p = s.to_span_payload()
        from agentobs.namespaces.trace import GenAISystem
        assert p.model.system == GenAISystem.OLLAMA

    def test_error_status_propagated(self):
        with pytest.raises(ValueError):
            with tracer.span("e") as s:
                raise ValueError("fail")
        p = s.to_span_payload()
        assert p.status == "error"
        assert p.error == "fail"

    def test_attributes_in_payload(self):
        with tracer.span("x", attributes={"a": 1}) as s:
            s.set_attribute("b", 2)
        p = s.to_span_payload()
        assert p.attributes == {"a": 1, "b": 2}

    def test_no_attributes_gives_none(self):
        with tracer.span("x") as s:
            ...
        p = s.to_span_payload()
        # Empty attributes dict is coerced to None in to_span_payload
        assert p.attributes is None

    def test_to_dict_serialisable(self):
        with tracer.span("x", model="gpt-4o") as s:
            ...
        d = s.to_span_payload().to_dict()
        assert isinstance(json.dumps(d), str)

    def test_unknown_operation_stored_as_string(self):
        with tracer.span("x", operation="custom_op") as s:
            ...
        p = s.to_span_payload()
        assert p.operation == "custom_op"


# ===========================================================================
# 9. Trace class
# ===========================================================================


class TestTraceClass:
    def test_start_trace_returns_trace(self):
        trace = start_trace("my-agent")
        try:
            assert isinstance(trace, TraceClass)
        finally:
            trace.end()

    def test_trace_has_agent_name(self):
        trace = start_trace("research")
        try:
            assert trace.agent_name == "research"
        finally:
            trace.end()

    def test_trace_has_trace_id(self):
        trace = start_trace("agent")
        try:
            assert len(trace.trace_id) == 32
        finally:
            trace.end()

    def test_trace_has_start_time(self):
        before = time.time()
        trace = start_trace("agent")
        after = time.time()
        try:
            assert before <= trace.start_time <= after
        finally:
            trace.end()

    def test_trace_end_idempotent(self):
        trace = start_trace("agent")
        trace.end()
        trace.end()  # second call must not raise

    def test_trace_as_context_manager(self):
        with start_trace("agent") as trace:
            assert isinstance(trace, TraceClass)

    def test_trace_context_manager_handles_exception(self):
        with pytest.raises(ValueError):
            with start_trace("agent") as trace:
                raise ValueError("trace error")
        assert trace._ended

    def test_trace_async_context_manager(self):
        async def main():
            async with start_trace("agent") as trace:
                return trace.agent_name

        name = asyncio.run(main())
        assert name == "agent"

    def test_trace_async_context_manager_exception(self):
        async def main():
            with pytest.raises(RuntimeError):
                async with start_trace("agent") as trace:
                    raise RuntimeError("async trace fail")
            return trace._ended

        ended = asyncio.run(main())
        assert ended

    def test_start_trace_empty_name_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            start_trace("")

    def test_start_trace_non_string_raises(self):
        with pytest.raises(ValueError):
            start_trace(None)  # type: ignore[arg-type]

    def test_span_inside_trace_inherits_trace_id(self):
        with start_trace("agent") as trace:
            with trace.span("child") as child:
                assert child.trace_id == trace.trace_id

    def test_llm_call_inside_trace(self):
        with start_trace("agent") as trace:
            with trace.llm_call(model="gpt-4o") as s:
                assert "llm_call" in s.name
                assert s.model == "gpt-4o"

    def test_tool_call_inside_trace(self):
        with start_trace("agent") as trace:
            with trace.tool_call("search") as s:
                assert "search" in s.name

    def test_generic_span_inside_trace(self):
        with start_trace("agent") as trace:
            with trace.span("generic", model="gpt-4o") as s:
                assert s.name == "generic"

    def test_nested_spans_under_trace(self):
        with start_trace("agent") as trace:
            with trace.llm_call(model="gpt-4o") as outer:
                with trace.span("inner") as inner:
                    assert inner.parent_span_id == outer.span_id
                    assert inner.trace_id == outer.trace_id

    def test_run_stack_active_inside_trace(self):
        with start_trace("agent") as trace:
            stack = _run_stack_var.get()
            assert len(stack) == 1
            assert stack[0].trace_id == trace.trace_id

    def test_run_stack_empty_after_trace_end(self):
        with start_trace("agent"):
            ...
        assert _run_stack_var.get() == ()

    def test_tracer_start_trace_method(self):
        """tracer.start_trace() is a convenience alias for start_trace()."""
        with tracer.start_trace("my-agent") as trace:
            assert trace.agent_name == "my-agent"

    # ------------------------------------------------------------------
    # span collection
    # ------------------------------------------------------------------

    def test_spans_collected(self):
        with start_trace("agent") as trace:
            with trace.llm_call(model="gpt-4o"):
                ...
            with trace.tool_call("search"):
                ...
        assert len(trace._spans) == 2

    def test_collected_spans_have_correct_names(self):
        with start_trace("agent") as trace:
            with trace.llm_call(model="gpt-4o"):
                ...
            with trace.tool_call("search"):
                ...
        names = [span.name for span in trace._spans]
        assert any("llm_call" in n for n in names)
        assert any("search" in n for n in names)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def test_to_json_returns_string(self):
        with start_trace("agent") as trace:
            with trace.llm_call(model="gpt-4o"):
                ...
        j = trace.to_json()
        assert isinstance(j, str)
        data = json.loads(j)
        assert data["agent_name"] == "agent"

    def test_to_json_includes_trace_id(self):
        with start_trace("agent") as trace:
            ...
        data = json.loads(trace.to_json())
        assert data["trace_id"] == trace.trace_id

    def test_to_json_spans_array(self):
        with start_trace("agent") as trace:
            with trace.llm_call(model="gpt-4o"):
                ...
        data = json.loads(trace.to_json())
        assert isinstance(data["spans"], list)
        assert len(data["spans"]) == 1

    def test_to_json_indent(self):
        with start_trace("agent") as trace:
            ...
        j = trace.to_json(indent=2)
        assert "\n" in j  # indented

    def test_save_creates_ndjson(self, tmp_jsonl):
        with start_trace("agent") as trace:
            with trace.llm_call(model="gpt-4o"):
                ...
            with trace.tool_call("search"):
                ...
        trace.save(str(tmp_jsonl))
        lines = tmp_jsonl.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            d = json.loads(line)
            assert "span_id" in d

    def test_save_empty_trace_creates_empty_file(self, tmp_jsonl):
        with start_trace("agent") as trace:
            ...
        trace.save(str(tmp_jsonl))
        assert tmp_jsonl.read_text() == ""

    def test_print_tree_runs_without_error(self, capsys):
        with start_trace("agent") as trace:
            ...
        trace.print_tree()  # now implemented — should not raise
        captured = capsys.readouterr()
        assert isinstance(captured.out, str)

    def test_summary_returns_dict(self):
        with start_trace("agent") as trace:
            ...
        result = trace.summary()
        assert isinstance(result, dict)


# ===========================================================================
# 10. start_trace() module-level function
# ===========================================================================


class TestStartTraceFunction:
    def test_agentobs_start_trace_exported(self):
        assert hasattr(agentobs, "start_trace")

    def test_copy_context_exported(self):
        assert hasattr(agentobs, "copy_context")

    def test_trace_class_exported(self):
        assert hasattr(agentobs, "Trace")

    def test_start_trace_integrates_with_tracer_agent_run(self):
        """start_trace() + tracer.agent_step() share the same run context."""
        with start_trace("agent") as trace:
            with tracer.agent_step("step") as step:
                assert step.agent_run_id == trace._run_ctx.agent_run_id


# ===========================================================================
# 11. Multiple concurrent traces (asyncio)
# ===========================================================================


class TestConcurrentTraces:
    def test_two_concurrent_traces_isolated(self):
        """Two concurrent asyncio traces must not bleed trace_ids."""

        async def run_trace(name: str, results: dict):
            async with start_trace(name) as trace:
                await asyncio.sleep(0)
                async with trace.llm_call(model="gpt-4o") as span:
                    await asyncio.sleep(0)
                    results[name] = span.trace_id

        async def main():
            results: dict = {}
            await asyncio.gather(
                run_trace("agent-1", results),
                run_trace("agent-2", results),
            )
            return results

        results = asyncio.run(main())
        assert results["agent-1"] != results["agent-2"]

    def test_many_concurrent_spans_all_unique(self):
        async def make_span(results: list):
            async with tracer.span("x") as s:
                await asyncio.sleep(0)
                results.append(s.span_id)

        async def main():
            results: list = []
            await asyncio.gather(*[make_span(results) for _ in range(50)])
            return results

        results = asyncio.run(main())
        assert len(results) == len(set(results))  # all unique

    def test_concurrent_tasks_each_see_own_stack_depth(self):
        async def nested(depth: int, results: dict, key: str):
            if depth == 0:
                results[key] = len(_span_stack_var.get())
                return
            async with tracer.span(f"level-{depth}"):
                await asyncio.sleep(0)
                await nested(depth - 1, results, key)

        async def main():
            results: dict = {}
            await asyncio.gather(
                nested(3, results, "a"),
                nested(5, results, "b"),
            )
            return results

        results = asyncio.run(main())
        assert results["a"] == 3
        assert results["b"] == 5


# ===========================================================================
# 12. Thread-based context propagation via copy_context()
# ===========================================================================


class TestThreadPropagationViaCopyContext:
    def test_executor_with_copy_context(self):
        """run_in_executor via copy_context sees parent span."""
        captured: list = []

        with tracer.span("parent-span") as parent:
            ctx = copy_context()

            def work():
                stack = _span_stack_var.get()
                captured.extend([s.span_id for s in stack])

            t = threading.Thread(target=lambda: ctx.run(work))
            t.start()
            t.join()

        assert parent.span_id in captured

    def test_thread_without_copy_context_sees_empty_stack(self):
        """Thread without copy_context() gets the default empty stack."""
        captured: list = []

        with tracer.span("parent"):
            t = threading.Thread(target=lambda: captured.append(_span_stack_var.get()))
            t.start()
            t.join()

        assert captured[0] == ()


# ===========================================================================
# 13. Edge cases and regression guards
# ===========================================================================


class TestEdgeCases:
    def test_span_context_manager_reuse_raises_on_double_exit(self):
        """Using the same SpanContextManager twice is not supported but
        should not corrupt global state."""
        cm = SpanContextManager("reuse")
        with cm:
            ...
        # Second use generates a fresh span without corrupting the stack
        with cm:
            ...
        assert _span_stack_var.get() == ()

    def test_span_context_manager_repr_before_enter(self):
        cm = SpanContextManager("x")
        assert cm._span is None  # not entered yet

    def test_agent_run_duration_ms_set(self):
        with tracer.agent_run("a") as run:
            ...
        assert run.duration_ms is not None
        assert run.duration_ms >= 0

    def test_agent_step_duration_ms_set(self):
        with tracer.agent_run("a"):
            with tracer.agent_step("s") as step:
                ...
        assert step.duration_ms is not None

    def test_no_agent_run_id_outside_agent_context(self):
        with tracer.span("x") as s:
            assert s.agent_run_id is None

    def test_agent_run_id_inherited_by_span_inside_run(self):
        with tracer.agent_run("a") as run:
            with tracer.span("s") as s:
                assert s.agent_run_id == run.agent_run_id

    def test_empty_trace_to_json(self):
        trace = start_trace("empty")
        trace.end()
        data = json.loads(trace.to_json())
        assert data["spans"] == []

    def test_span_span_id_16_chars(self):
        with tracer.span("x") as s:
            assert len(s.span_id) == 16

    def test_span_all_different_ids(self):
        spans = []
        for _ in range(20):
            with tracer.span("x") as s:
                spans.append(s.span_id)
        assert len(set(spans)) == 20

    def test_gemini_model_system_inferred(self):
        from agentobs._span import _resolve_model_info
        from agentobs.namespaces.trace import GenAISystem
        info = _resolve_model_info("gemini-1.5-pro")
        assert info.system == GenAISystem.VERTEX_AI

    def test_cohere_model_system_inferred(self):
        from agentobs._span import _resolve_model_info
        from agentobs.namespaces.trace import GenAISystem
        info = _resolve_model_info("command-r-plus")
        assert info.system == GenAISystem.COHERE

    def test_phi_model_system_ollama(self):
        from agentobs._span import _resolve_model_info
        from agentobs.namespaces.trace import GenAISystem
        info = _resolve_model_info("phi-3")
        assert info.system == GenAISystem.OLLAMA

    def test_qwen_model_system_ollama(self):
        from agentobs._span import _resolve_model_info
        from agentobs.namespaces.trace import GenAISystem
        info = _resolve_model_info("qwen-2.5")
        assert info.system == GenAISystem.OLLAMA


# ---------------------------------------------------------------------------
# TestCoverageGapClosers — targeted tests to reach ≥95 % on _span.py
# ---------------------------------------------------------------------------


class TestCoverageGapClosers:
    """Tests targeting the 19 remaining uncovered statements / 5 partial branches."""

    # ------------------------------------------------------------------
    # Lines 196, 200 — Span.set_token_usage / Span.set_cost
    # ------------------------------------------------------------------

    def test_span_set_token_usage(self):
        """Span.set_token_usage() stores the object (line 196)."""
        from agentobs.namespaces.trace import TokenUsage
        with tracer.span("s") as s:
            tu = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
            s.set_token_usage(tu)
        assert s.token_usage is tu
        assert s.token_usage.total_tokens == 15

    def test_span_set_cost(self):
        """Span.set_cost() stores the object (line 200)."""
        from agentobs.namespaces.trace import CostBreakdown
        with tracer.span("s") as s:
            cb = CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.002, total_cost_usd=0.003)
            s.set_cost(cb)
        assert s.cost is cb
        assert s.cost.total_cost_usd == pytest.approx(0.003)

    # ------------------------------------------------------------------
    # Lines 349-351 — SpanContextManager.__exit__ emit error handler
    # ------------------------------------------------------------------

    def test_span_cm_emit_error_handled(self, monkeypatch):
        """When emit_span raises, _handle_export_error is called (lines 349-351)."""
        import agentobs._stream as stream_mod
        handled: list[Exception] = []
        monkeypatch.setattr(stream_mod, "emit_span", lambda span: (_ for _ in ()).throw(RuntimeError("boom")))  # NOSONAR — generator.throw() trick for lambda
        monkeypatch.setattr(stream_mod, "_handle_export_error", lambda exc: handled.append(exc))
        with tracer.span("x"):
            ...
        assert len(handled) == 1
        assert "boom" in str(handled[0])

    # ------------------------------------------------------------------
    # Line 415→exit — AgentStepContext.end() idempotent false branch
    # ------------------------------------------------------------------

    def test_agent_step_context_end_idempotent(self):
        """Calling end() twice does not overwrite end_ns (line 415→exit)."""
        with tracer.agent_run("a"):
            with tracer.agent_step("s") as step:
                step.end()  # first call inside the block
                first_end_ns = step.end_ns
        # __exit__ calls end() again — end_ns must stay the same
        assert step.end_ns == first_end_ns

    # ------------------------------------------------------------------
    # Lines 424-425 — to_agent_step_payload() unknown operation
    # ------------------------------------------------------------------

    def test_agent_step_payload_unknown_operation(self):
        """Unknown operation strings survive serialisation (lines 424-425)."""
        with tracer.agent_run("a"):
            with tracer.agent_step("s") as step:
                step.operation = "custom_not_in_enum"
        payload = step.to_agent_step_payload()
        assert payload.operation == "custom_not_in_enum"

    # ------------------------------------------------------------------
    # Lines 509→513 — AgentStepContextManager.__exit__ no-run-ctx branch
    # ------------------------------------------------------------------

    def test_agent_step_cm_exit_when_run_stack_cleared(self):
        """Defensive branch: step __exit__ when run stack is unexpectedly empty (509→513)."""
        from agentobs._span import AgentStepContextManager, _run_stack_var
        with tracer.agent_run("a"):
            scm = AgentStepContextManager("step")
            scm.__enter__()
            # Forcibly hollow out the run stack between enter and exit
            token = _run_stack_var.set(())
            try:
                scm.__exit__(None, None, None)
            finally:
                _run_stack_var.reset(token)

    # ------------------------------------------------------------------
    # Lines 517-519 — AgentStepContextManager.__exit__ emit error handler
    # ------------------------------------------------------------------

    def test_agent_step_cm_emit_error_handled(self, monkeypatch):
        """When emit_agent_step raises, _handle_export_error is called (lines 517-519)."""
        import agentobs._stream as stream_mod
        handled: list[Exception] = []
        monkeypatch.setattr(stream_mod, "emit_agent_step", lambda ctx: (_ for _ in ()).throw(RuntimeError("step-boom")))  # NOSONAR — generator.throw() trick for lambda
        monkeypatch.setattr(stream_mod, "_handle_export_error", lambda exc: handled.append(exc))
        with tracer.agent_run("a"):
            with tracer.agent_step("s"):
                ...
        assert len(handled) == 1
        assert "step-boom" in str(handled[0])

    # ------------------------------------------------------------------
    # Line 576→exit — AgentRunContext.end() idempotent false branch
    # ------------------------------------------------------------------

    def test_agent_run_context_end_idempotent(self):
        """Calling end() twice preserves the first end_ns (line 576→exit)."""
        with tracer.agent_run("a") as run:
            run.end()  # explicit first call
            first_end_ns = run.end_ns
        # __exit__ calls end() again — should be a no-op
        assert run.end_ns == first_end_ns

    # ------------------------------------------------------------------
    # Lines 594-601 — to_agent_run_payload() with token_usage and cost
    # ------------------------------------------------------------------

    def test_agent_run_payload_aggregates_token_usage_and_cost(self):
        """to_agent_run_payload aggregates token/cost from steps (lines 594-601)."""
        from agentobs.namespaces.trace import TokenUsage, CostBreakdown
        with tracer.agent_run("a") as run:
            with tracer.agent_step("s1") as step1:
                step1.token_usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
                step1.cost = CostBreakdown(input_cost_usd=0.01, output_cost_usd=0.005, total_cost_usd=0.015)
            with tracer.agent_step("s2") as step2:
                step2.token_usage = TokenUsage(input_tokens=20, output_tokens=8, total_tokens=28)
                step2.cost = CostBreakdown(input_cost_usd=0.02, output_cost_usd=0.008, total_cost_usd=0.028)
        payload = run.to_agent_run_payload()
        assert payload.total_token_usage.input_tokens == 30
        assert payload.total_token_usage.output_tokens == 13
        assert payload.total_token_usage.total_tokens == 43
        assert payload.total_cost.total_cost_usd == pytest.approx(0.043)
        assert payload.total_model_calls == 2

    # ------------------------------------------------------------------
    # Lines 672-674 — AgentRunContextManager.__exit__ emit error handler
    # ------------------------------------------------------------------

    def test_agent_run_cm_emit_error_handled(self, monkeypatch):
        """When emit_agent_run raises, _handle_export_error is called (lines 672-674)."""
        import agentobs._stream as stream_mod
        handled: list[Exception] = []
        monkeypatch.setattr(stream_mod, "emit_agent_run", lambda ctx: (_ for _ in ()).throw(RuntimeError("run-boom")))  # NOSONAR — generator.throw() trick for lambda
        monkeypatch.setattr(stream_mod, "_handle_export_error", lambda exc: handled.append(exc))
        with tracer.agent_run("a"):
            ...
        assert len(handled) == 1
        assert "run-boom" in str(handled[0])

    def test_start_trace_with_attributes_in_json(self):
        """Attributes passed to start_trace are stored and appear in to_json."""
        trace = start_trace("bot", env="prod", version="2")
        trace.end()
        data = json.loads(trace.to_json())
        assert data["attributes"] == {"env": "prod", "version": "2"}

    def test_start_trace_no_attributes_no_key_in_json(self):
        """Traces with no attributes omit the 'attributes' key from to_json."""
        trace = start_trace("bot")
        trace.end()
        data = json.loads(trace.to_json())
        assert "attributes" not in data
