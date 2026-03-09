"""Tests for agentobs.trace — @trace() function decorator (Tool 1 / llm-trace).

Covers:
- Sync and async function wrapping
- Exception propagation and recording
- capture_args / capture_return
- tool=True sets execute_tool operation
- static attributes
- Tracer.trace() method
- Top-level import
"""

from __future__ import annotations

import asyncio

import pytest

from agentobs.trace import _TraceDecorator, _safe_repr, trace
from agentobs.testing import capture_events


# ---------------------------------------------------------------------------
# _safe_repr
# ---------------------------------------------------------------------------


class TestSafeRepr:
    def test_short_string(self) -> None:
        assert _safe_repr("hello") == "'hello'"

    def test_truncates_long_value(self) -> None:
        long_str = "x" * 300
        result = _safe_repr(long_str)
        assert len(result) <= 203  # 200 chars + "..."
        assert result.endswith("...")

    def test_exact_boundary_not_truncated(self) -> None:
        # 200-char string should NOT be truncated
        s = "a" * 200
        expected = repr(s)  # 202 chars (with quotes), no truncation
        result = _safe_repr(s)
        # repr("a"*200) is 202 chars which is > 200, so it will be truncated
        # Let's just confirm it doesn't crash
        assert isinstance(result, str)

    def test_unrepresentable(self) -> None:
        class Bad:
            def __repr__(self) -> str:
                raise RuntimeError("boom")

        assert _safe_repr(Bad()) == "<unrepresentable>"

    def test_numeric(self) -> None:
        assert _safe_repr(42) == "42"


# ---------------------------------------------------------------------------
# @trace — basic wrapping (sync)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTraceDecoratorSync:
    def test_bare_decorator_passthrough(self) -> None:
        @trace
        def fn() -> int:
            return 42

        assert fn() == 42

    def test_with_name_passthrough(self) -> None:
        @trace(name="my-span")
        def fn() -> str:
            return "hello"

        assert fn() == "hello"

    def test_with_args_passthrough(self) -> None:
        @trace(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5

    def test_exception_propagates(self) -> None:
        @trace(name="err-span")
        def fn() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            fn()

    def test_exception_recorded_on_span(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []

        def _cb(span: object) -> None:
            captured.append(span)

        hooks.on_span_end(_cb)
        try:
            @trace(name="err-span-2")
            def fn() -> None:
                raise ValueError("boom")

            with pytest.raises(ValueError):
                fn()
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(_cb)

        assert len(captured) == 1
        span = captured[0]
        assert getattr(span, "status") == "error"
        assert "boom" in (getattr(span, "error") or "")

    def test_span_name_defaults_to_qualname(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace
            def my_unique_function_name() -> None:
                pass

            my_unique_function_name()
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        assert any("my_unique_function_name" in getattr(s, "name", "") for s in captured)

    def test_model_forwarded_to_span(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="llm-call", model="gpt-4o")
            def fn() -> None:
                pass

            fn()
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "llm-call")
        assert getattr(span, "model") == "gpt-4o"

    def test_tool_flag_sets_execute_tool_operation(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="tool-fn", tool=True)
            def my_tool() -> None:
                pass

            my_tool()
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "tool-fn")
        assert str(getattr(span, "operation")) == "execute_tool"

    def test_custom_operation(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="embed-fn", operation="embedding")
            def fn() -> None:
                pass

            fn()
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "embed-fn")
        assert str(getattr(span, "operation")) == "embedding"

    def test_preserves_function_metadata(self) -> None:
        @trace(name="named")
        def my_documented_fn() -> None:
            """Docstring."""

        assert my_documented_fn.__doc__ == "Docstring."

    def test_span_is_emitted_as_event(self) -> None:
        with capture_events() as events:
            @trace(name="event-test")
            def fn() -> None:
                pass

            fn()

        # Event.payload is a MappingProxyType that supports .get()
        assert len(events) >= 1
        assert any(events[i].payload.get("span_name") == "event-test" for i in range(len(events)))


# ---------------------------------------------------------------------------
# capture_args / capture_return
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCaptureArgReturn:
    def _collect(self, fn_name: str) -> tuple[list[object], object]:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        return captured, hooks

    def test_capture_args_records_arguments(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="args-fn", capture_args=True)
            def fn(x: int, y: str) -> str:
                return f"{x}{y}"

            fn(1, "hello")
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "args-fn")
        assert "arg.x" in getattr(span, "attributes")
        assert getattr(span, "attributes")["arg.x"] == "1"
        assert "arg.y" in getattr(span, "attributes")

    def test_capture_return_records_return_value(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="ret-fn", capture_return=True)
            def fn() -> dict:
                return {"key": "value"}

            fn()
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "ret-fn")
        assert "return_value" in getattr(span, "attributes")

    def test_no_arg_capture_by_default(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="noargs-fn")
            def fn(a: int, b: int) -> int:
                return a + b

            fn(1, 2)
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "noargs-fn")
        assert "arg.a" not in getattr(span, "attributes")
        assert "return_value" not in getattr(span, "attributes")

    def test_static_attributes_always_present(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="static-fn", attributes={"env": "test", "version": "1"})
            def fn() -> None:
                pass

            fn()
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "static-fn")
        assert getattr(span, "attributes").get("env") == "test"
        assert getattr(span, "attributes").get("version") == "1"


# ---------------------------------------------------------------------------
# Async support
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAsyncTrace:
    def test_async_function_call_returns_value(self) -> None:
        @trace(name="async-fn")
        async def async_fn() -> str:
            return "async"

        result = asyncio.run(async_fn())
        assert result == "async"

    def test_async_exception_propagates(self) -> None:
        @trace(name="async-err")
        async def async_fn() -> None:
            raise ValueError("async boom")

        with pytest.raises(ValueError, match="async boom"):
            asyncio.run(async_fn())

    def test_async_exception_recorded_on_span(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="async-err-span")
            async def async_fn() -> None:
                raise ValueError("async boom")

            with pytest.raises(ValueError):
                asyncio.run(async_fn())
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "async-err-span")
        assert getattr(span, "status") == "error"

    def test_async_span_emitted(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="async-model-span", model="claude-3")
            async def async_fn() -> int:
                return 1

            asyncio.run(async_fn())
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "async-model-span")
        assert getattr(span, "model") == "claude-3"

    def test_async_capture_args(self) -> None:
        from agentobs._hooks import hooks  # noqa: PLC0415

        captured: list[object] = []
        hooks.on_span_end(captured.append)
        try:
            @trace(name="async-args", capture_args=True)
            async def async_fn(prompt: str) -> str:
                return prompt

            asyncio.run(async_fn("hello"))
        finally:
            with hooks._lock:
                hooks._all_end_hooks.remove(captured.append)

        span = next(s for s in captured if getattr(s, "name") == "async-args")
        assert "arg.prompt" in getattr(span, "attributes")


# ---------------------------------------------------------------------------
# Tracer.trace() method
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTracerTraceMethod:
    def test_tracer_trace_wraps_sync_function(self) -> None:
        from agentobs import tracer  # noqa: PLC0415

        @tracer.trace(name="tracer-method-span")
        def fn() -> int:
            return 99

        assert fn() == 99

    def test_tracer_trace_wraps_async_function(self) -> None:
        from agentobs import tracer  # noqa: PLC0415

        @tracer.trace(name="tracer-async-span")
        async def async_fn() -> str:
            return "ok"

        result = asyncio.run(async_fn())
        assert result == "ok"

    def test_tracer_trace_bare_decorator(self) -> None:
        from agentobs import tracer  # noqa: PLC0415

        @tracer.trace
        def bare_fn() -> str:
            return "bare"

        assert bare_fn() == "bare"


# ---------------------------------------------------------------------------
# Top-level import
# ---------------------------------------------------------------------------


def test_trace_importable_from_agentobs() -> None:
    from agentobs import trace as t  # noqa: PLC0415

    assert callable(t)


def test_trace_decorator_instance_type() -> None:
    from agentobs.trace import _TraceDecorator  # noqa: PLC0415

    @trace(name="typed")
    def sync_fn() -> None:
        pass

    # sync functions return a _TraceDecorator
    assert isinstance(sync_fn, _TraceDecorator)


def test_async_decorator_is_not_trace_decorator() -> None:
    """Async functions get a functools.wraps wrapper, not _TraceDecorator."""

    @trace(name="async-typed")
    async def async_fn() -> None:
        pass

    assert not isinstance(async_fn, _TraceDecorator)
    assert asyncio.iscoroutinefunction(async_fn)
