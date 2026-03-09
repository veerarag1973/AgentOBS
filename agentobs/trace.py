"""agentobs.trace — @trace() function decorator and trace engine (Tool 1 / llm-trace).

Single decorator that instruments any Python function as an AgentOBS span::

    from agentobs import trace

    @trace(name="search", model="gpt-4o")
    def call_llm(prompt: str) -> str: ...

    @trace(name="async-step")
    async def async_step(x: int) -> dict: ...

    @trace(name="web-search", tool=True)
    def search_web(query: str) -> list[str]: ...

Supports:
- Sync and async functions/methods
- Auto-capture of call arguments and return values (opt-in)
- Parent-child span relationships via contextvars
- ``tool=True`` to emit spans with ``operation="execute_tool"``
- Pytest fixture integration via :func:`~agentobs.testing.captured_spans`
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any, Callable, TypeVar

from agentobs._span import SpanContextManager

__all__ = ["trace"]

_F = TypeVar("_F", bound=Callable[..., Any])


def _safe_repr(value: Any, max_len: int = 200) -> str:
    """Return a repr of *value* truncated to *max_len* characters."""
    try:
        r = repr(value)
    except Exception:
        r = "<unrepresentable>"
    return r[:max_len] + "..." if len(r) > max_len else r


class _TraceDecorator:
    """Wraps a callable so every invocation is recorded as an AgentOBS span.

    Created by :func:`trace`; use :func:`trace` rather than instantiating
    this class directly.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        name: str | None,
        model: str | None,
        operation: str,
        tool: bool,
        capture_args: bool,
        capture_return: bool,
        attributes: dict[str, Any] | None,
    ) -> None:
        self._fn = fn
        self._name = name or fn.__qualname__
        self._model = model
        self._operation = operation
        self._tool = tool
        self._capture_args = capture_args
        self._capture_return = capture_return
        self._attributes = dict(attributes or {})
        # Preserve __name__, __doc__, __module__, etc. on the wrapper.
        functools.update_wrapper(self, fn)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_attrs(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        """Build the initial span attributes dict from static attrs + captured args."""
        attrs: dict[str, Any] = dict(self._attributes)
        if self._tool:
            # Mark span so InspectorSession can detect it even without checking operation.
            attrs["tool"] = True
        if self._capture_args or self._tool:
            try:
                sig = inspect.signature(self._fn)
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                for k, v in bound.arguments.items():
                    attrs[f"arg.{k}"] = _safe_repr(v)
            except (TypeError, ValueError):
                pass
        return attrs

    def _record_return(self, span: Any, result: Any) -> None:
        if self._capture_return or self._tool:
            span.set_attribute("return_value", _safe_repr(result))

    # ------------------------------------------------------------------
    # Sync call
    # ------------------------------------------------------------------

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        attrs = self._build_attrs(args, kwargs)
        cm = SpanContextManager(
            name=self._name,
            model=self._model,
            operation=self._operation,
            attributes=attrs,
        )
        with cm as span:
            result = self._fn(*args, **kwargs)
            self._record_return(span, result)
        return result


def _make_async_wrapper(decorator: _TraceDecorator, fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return an async wrapper that runs *fn* inside a span context."""

    @functools.wraps(fn)
    async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
        attrs = decorator._build_attrs(args, kwargs)
        cm = SpanContextManager(
            name=decorator._name,
            model=decorator._model,
            operation=decorator._operation,
            attributes=attrs,
        )
        async with cm as span:
            result = await fn(*args, **kwargs)
            decorator._record_return(span, result)
        return result

    return _async_wrapper


def trace(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    model: str | None = None,
    operation: str = "chat",
    tool: bool = False,
    capture_args: bool = False,
    capture_return: bool = False,
    attributes: dict[str, Any] | None = None,
) -> Any:
    """Decorator that instruments a function as an AgentOBS span.

    Works with or without parentheses::

        @trace
        def my_fn(): ...

        @trace(name="custom-name", model="gpt-4o")
        def my_fn(): ...

    Args:
        fn:             The function to wrap (only when used bare as ``@trace``).
        name:           Span name.  Defaults to ``fn.__qualname__``.
        model:          Model identifier string forwarded to ``SpanPayload``.
        operation:      GenAI operation name (default ``"chat"``).  Any
                        :class:`~agentobs.namespaces.trace.GenAIOperationName`
                        value or a custom string.
        tool:           When ``True``, marks this as a tool call; sets
                        ``operation="execute_tool"`` regardless of *operation*.
        capture_args:   When ``True``, records call arguments as ``arg.<name>``
                        span attributes (values truncated to 200 chars).
        capture_return: When ``True``, records the return value as a
                        ``return_value`` span attribute (truncated to 200 chars).
        attributes:     Static key-value attributes added to every span.

    Returns:
        Decorated callable (sync or async), or a single-argument decorator
        when keyword arguments are supplied.
    """

    def _decorate(f: Callable[..., Any]) -> Callable[..., Any]:
        effective_op = "execute_tool" if tool else operation
        dec = _TraceDecorator(
            fn=f,
            name=name,
            model=model,
            operation=effective_op,
            tool=tool,
            capture_args=capture_args,
            capture_return=capture_return,
            attributes=attributes,
        )
        if asyncio.iscoroutinefunction(f):
            return _make_async_wrapper(dec, f)
        return dec

    if fn is not None:
        # @trace — bare decorator, no parentheses
        return _decorate(fn)
    # @trace(...) — decorator factory
    return _decorate
