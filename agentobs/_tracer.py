"""agentobs._tracer — :class:`Tracer` class and module-level ``tracer`` singleton.

The :class:`Tracer` is the primary entry point for instrumenting code with
AgentOBS.  Import the module-level singleton ``tracer`` and use its context
managers to create spans and agent traces::

    from agentobs import tracer, configure

    configure(exporter="console")

    with tracer.span("chat", model="gpt-4o") as s:
        s.set_attribute("prompt_tokens", 512)

    with tracer.agent_run("research-agent") as run:
        with tracer.agent_step("web-search") as step:
            step.set_attribute("query", "what is RAG?")
        with tracer.agent_step("summarize"):
            pass

    # Imperative trace API (Phase 1)
    trace = tracer.start_trace("research-agent")
    with trace.llm_call(model="gpt-4o"):
        ...
    trace.end()

All context managers support both ``with`` and ``async with``.  Async usage
is safe because the span stack is backed by :mod:`contextvars` so each asyncio
task sees its own stack slice.
"""

from __future__ import annotations

from typing import Any

from agentobs._span import (
    AgentRunContextManager,
    AgentStepContextManager,
    SpanContextManager,
)
from agentobs._trace import Trace, start_trace as _start_trace
from agentobs.trace import trace as _trace_decorator, _TraceDecorator

__all__ = ["Tracer", "tracer"]


class Tracer:
    """The AgentOBS tracing façade.

    A single module-level instance is created as :data:`tracer` and is the
    recommended way to instrument code.  Creating additional :class:`Tracer`
    instances is supported but shares the same thread-local context stacks.

    All ``span``/``agent_run``/``agent_step`` methods return context managers
    that push the new context onto the thread-local stack on ``__enter__`` and
    pop it (and emit the event) on ``__exit__``.
    """

    # ------------------------------------------------------------------
    # Span API  (Phase 2)
    # ------------------------------------------------------------------

    def span(
        self,
        name: str,
        *,
        model: str | None = None,
        operation: str = "chat",
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SpanContextManager:
        """Create a new :class:`~agentobs._span.SpanContextManager`.

        Use as a context manager::

            with tracer.span("llm-call", model="gpt-4o", temperature=0.7) as s:
                s.set_attribute("prompt_tokens", 512)

        Args:
            name:        Human-readable span name (non-empty string).
            model:       Model name string (e.g. ``"gpt-4o"``).  Used to infer
                         the provider when no integration has set
                         :attr:`~agentobs._span.Span.token_usage`.
            operation:   GenAI operation name (default ``"chat"``).  Any
                         :class:`~agentobs.namespaces.trace.GenAIOperationName`
                         value or a custom string.
            temperature: Sampling temperature forwarded to :class:`SpanPayload`.
            top_p:       Nucleus sampling ``top_p`` value.
            max_tokens:  Maximum token limit for this LLM call.
            attributes:  Initial key-value attributes.  Additional attributes
                         can be added inside the block via
                         :meth:`~agentobs._span.Span.set_attribute`.

        Returns:
            A :class:`~agentobs._span.SpanContextManager` that yields a
            :class:`~agentobs._span.Span` on ``__enter__``.
        """
        return SpanContextManager(
            name=name,
            model=model,
            operation=operation,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            attributes=attributes,
        )

    # ------------------------------------------------------------------
    # Agent API  (Phase 4)
    # ------------------------------------------------------------------

    def agent_run(self, agent_name: str) -> AgentRunContextManager:
        """Create a root agent-run context manager.

        Use as an outer context that wraps one or more ``agent_step`` calls::

            with tracer.agent_run("my-agent") as run:
                with tracer.agent_step("step-1"):
                    ...

        On exit, emits an
        :data:`~agentobs.types.EventType.TRACE_AGENT_COMPLETED` event with
        aggregated totals across all child steps.

        Args:
            agent_name: Name of the agent (non-empty string).

        Returns:
            :class:`~agentobs._span.AgentRunContextManager`
        """
        return AgentRunContextManager(agent_name=agent_name)

    def agent_step(
        self,
        step_name: str,
        *,
        operation: str = "invoke_agent",
        attributes: dict[str, Any] | None = None,
    ) -> AgentStepContextManager:
        """Create a single agent-step context manager.

        Must be used inside an ``agent_run`` block::

            with tracer.agent_run("my-agent"):
                with tracer.agent_step("search") as step:
                    step.set_attribute("query", "hello")

        On exit, emits an
        :data:`~agentobs.types.EventType.TRACE_AGENT_STEP` event.

        Args:
            step_name:  Human-readable step name.
            operation:  GenAI operation name (default ``"invoke_agent"``).
            attributes: Initial key-value attributes.

        Returns:
            :class:`~agentobs._span.AgentStepContextManager`

        Raises:
            RuntimeError: If called outside an ``agent_run`` context.
        """
        return AgentStepContextManager(
            step_name=step_name,
            operation=operation,
            attributes=attributes,
        )

    # ------------------------------------------------------------------
    # Trace API  (Phase 1)
    # ------------------------------------------------------------------

    def start_trace(self, agent_name: str, **attributes: Any) -> Trace:
        """Start a new agent trace and return a :class:`~agentobs._trace.Trace` handle.

        Convenience wrapper around the module-level :func:`~agentobs._trace.start_trace`.
        The returned :class:`Trace` must be closed with :meth:`~agentobs._trace.Trace.end`
        or used as a context manager::

            with tracer.start_trace("research-agent") as trace:
                with trace.llm_call(model="gpt-4o") as s:
                    ...

        Args:
            agent_name:   Name of the agent being executed.
            **attributes: Optional key-value attributes for the root run context.

        Returns:
            :class:`~agentobs._trace.Trace`
        """
        return _start_trace(agent_name, **attributes)

    def trace(
        self,
        fn: Any = None,
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

        Delegates to :func:`~agentobs.trace.trace`.  Provided as a convenience
        method so callers who already hold a :class:`Tracer` reference do not
        need a separate import::

            @tracer.trace(name="my-step", model="gpt-4o")
            def call_llm(prompt: str) -> str: ...

            @tracer.trace(name="async-step")
            async def async_step(x: int) -> dict: ...

        Args:
            fn:             Function to wrap when used bare (``@tracer.trace``).
            name:           Span name (defaults to ``fn.__qualname__``).
            model:          Model identifier string.
            operation:      GenAI operation name (default ``"chat"``).
            tool:           Mark as tool call; sets operation to ``"execute_tool"``.
            capture_args:   Record call arguments as span attributes.
            capture_return: Record return value as a span attribute.
            attributes:     Static key-value attributes on every span.

        Returns:
            Decorated callable, or a single-argument decorator.
        """
        return _trace_decorator(
            fn,
            name=name,
            model=model,
            operation=operation,
            tool=tool,
            capture_args=capture_args,
            capture_return=capture_return,
            attributes=attributes,
        )


# ---------------------------------------------------------------------------
# Module-level singleton — ``from agentobs import tracer``
# ---------------------------------------------------------------------------

#: The default :class:`Tracer` singleton.
#:
#: Import this directly for convenience::
#:
#:     from agentobs import tracer
#:     with tracer.span("my-span"):
#:         ...
tracer: Tracer = Tracer()
