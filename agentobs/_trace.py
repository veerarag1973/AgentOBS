"""agentobs._trace — :class:`Trace` object and :func:`start_trace` entry point.

A :class:`Trace` wraps a *root* :class:`~agentobs._span.AgentRunContextManager`
and gives callers a convenient imperative handle to the entire agent execution::

    trace = agentobs.start_trace("research_agent")

    with trace.llm_call(model="gpt-4o") as s:
        s.set_attribute("query", "latest AI papers")

    with trace.tool_call("search") as s:
        s.set_attribute("results_count", 5)

    trace.end()

The :class:`Trace` object also provides convenience methods for serialisation
(:meth:`to_json`, :meth:`save`) that will become richer in later phases when
debug utilities (:meth:`print_tree`, :meth:`summary`) are added.

Design notes
------------
* :func:`start_trace` opens the underlying :class:`AgentRunContextManager`
  immediately (``__enter__``), so all subsequently created spans automatically
  inherit the trace's ``trace_id``.
* :meth:`Trace.end` closes that context manager (``__exit__``).  Callers
  *must* call :meth:`end` or use the :class:`Trace` as a context manager::

      with agentobs.start_trace("my-agent") as trace:
          ...
          # trace.end() called automatically on exit

* :class:`Trace` collects emitted :class:`~agentobs._span.Span` instances via
  :meth:`_record_span` which is called by the stream module when a
  ``_TRACE_COLLECTOR`` attribute is present on the active
  :class:`~agentobs._span.AgentRunContext`.  This allows :meth:`to_json` and
  :meth:`save` to operate on in-memory data without re-reading from a file.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentobs._span import (
    AgentRunContext,
    AgentRunContextManager,
    Span,
    SpanContextManager,
    _span_id,
    _trace_id,
    _now_ns,
)

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["Trace", "start_trace"]


@dataclass
class Trace:
    """A handle to a complete agent trace.

    Created by :func:`start_trace`; do not construct directly.

    Attributes:
        agent_name:   Name of the agent being traced.
        trace_id:     32-char hex OTel-compatible trace ID.
        start_time:   Unix timestamp (float seconds) when the trace started.
    """

    agent_name: str
    trace_id: str
    start_time: float
    _run_ctx: AgentRunContext = field(repr=False)
    _run_cm: AgentRunContextManager = field(repr=False)
    attributes: dict[str, Any] = field(default_factory=dict)
    _spans: list[Span] = field(default_factory=list, repr=False)
    _ended: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------------
    # Span convenience methods
    # ------------------------------------------------------------------

    def llm_call(
        self,
        model: str | None = None,
        *,
        operation: str = "chat",
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SpanContextManager:
        """Open a child span for an LLM call within this trace.

        Example::

            with trace.llm_call(model="gpt-4o", temperature=0.7) as s:
                s.set_attribute("prompt_tokens", 512)
        """
        name = f"llm_call:{model}" if model else "llm_call"
        return SpanContextManager(
            name=name,
            model=model,
            operation=operation,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            attributes=attributes,
        )

    def tool_call(
        self,
        tool_name: str,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> SpanContextManager:
        """Open a child span for a tool call within this trace.

        Example::

            with trace.tool_call("search") as s:
                results = search(query)
        """
        return SpanContextManager(
            name=f"tool_call:{tool_name}",
            operation="tool",
            attributes=attributes,
        )

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
        """Open a generic child span within this trace."""
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
    # Internal span collection
    # ------------------------------------------------------------------

    def _record_span(self, span: Span) -> None:
        """Called by _stream.emit_span when this trace is the active run context."""
        self._spans.append(span)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def end(self) -> None:
        """Close the trace and emit the agent-run completion event.

        Idempotent — subsequent calls are no-ops.
        """
        if self._ended:
            return
        self._ended = True
        self._run_cm.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # Context manager protocol (allows ``with start_trace(...) as trace:``)
    # ------------------------------------------------------------------

    def __enter__(self) -> "Trace":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if not self._ended:
            self._run_cm.__exit__(exc_type, exc_val, exc_tb)
            self._ended = True
        return False

    async def __aenter__(self) -> "Trace":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        return self.__exit__(exc_type, exc_val, exc_tb)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialise the trace's collected spans to a JSON string.

        Returns a JSON object with ``trace_id``, ``agent_name``, ``start_time``,
        and a ``spans`` array of span payload dicts.

        Args:
            indent: Optional JSON indentation level (``None`` = compact).
        """
        return json.dumps(self._to_dict(), indent=indent, sort_keys=True, default=str)

    def save(self, path: str) -> None:
        """Write the trace as NDJSON (one span per line) to *path*.

        Args:
            path: File path to write to.  The file is created or overwritten.
        """
        lines = [
            json.dumps(span.to_span_payload().to_dict(), sort_keys=True, default=str)
            for span in self._spans
        ]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
            if lines:
                fh.write("\n")

    def _to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "trace_id": self.trace_id,
            "agent_name": self.agent_name,
            "start_time": self.start_time,
            "spans": [span.to_span_payload().to_dict() for span in self._spans],
        }
        if self.attributes:
            d["attributes"] = self.attributes
        return d

    # Placeholder methods for Phase 3 debug utilities.
    def print_tree(self, *, file: Any = None) -> None:
        """Pretty-print a hierarchical tree of spans to stdout.

        Delegates to :func:`agentobs.debug.print_tree`.
        Requires the trace to have ended (or have accumulated spans).
        """
        from agentobs.debug import print_tree  # noqa: PLC0415
        print_tree(self._spans, file=file)

    def summary(self) -> dict[str, Any]:
        """Return an aggregated statistics dict for the trace's spans.

        Delegates to :func:`agentobs.debug.summary`.
        """
        from agentobs.debug import summary  # noqa: PLC0415
        return summary(self._spans)

    def visualize(self, *, output: str = "html", path: str | None = None) -> str:
        """Generate a self-contained HTML Gantt timeline for this trace.

        Delegates to :func:`agentobs.debug.visualize`.

        Args:
            output: Output format — currently only ``"html"``.
            path:   Optional file path to write the HTML to.

        Returns:
            HTML string.
        """
        from agentobs.debug import visualize  # noqa: PLC0415
        return visualize(self._spans, output=output, path=path)


# ---------------------------------------------------------------------------
# start_trace()
# ---------------------------------------------------------------------------


def start_trace(agent_name: str, **attributes: Any) -> Trace:
    """Start a new agent trace and return a :class:`Trace` handle.

    Opens a root :class:`~agentobs._span.AgentRunContextManager` so all spans
    created within the trace automatically inherit the correct ``trace_id``.

    Must be closed by calling :meth:`Trace.end` or by using the returned
    object as a context manager::

        # Imperative style
        trace = agentobs.start_trace("research_agent")
        with trace.llm_call(model="gpt-4o"):
            ...
        trace.end()

        # Context manager style (recommended)
        with agentobs.start_trace("research_agent") as trace:
            with trace.llm_call(model="gpt-4o"):
                ...

    Args:
        agent_name:  Name of the agent being executed.
        **attributes: Optional key-value attributes stored on the
                      :attr:`Trace.attributes` dict and included in
                      :meth:`Trace.to_json` output.

    Returns:
        A :class:`Trace` object that acts as the root execution context.
    """
    if not isinstance(agent_name, str) or not agent_name:
        raise ValueError("start_trace: agent_name must be a non-empty string")

    cm = AgentRunContextManager(agent_name=agent_name)
    run_ctx = cm.__enter__()

    # Attach a back-reference so _stream can call _record_span on this Trace.
    trace = Trace(
        agent_name=agent_name,
        trace_id=run_ctx.trace_id,
        start_time=time.time(),
        _run_ctx=run_ctx,
        _run_cm=cm,
        attributes=dict(attributes) if attributes else {},
    )
    # Store the Trace on the AgentRunContext so _stream.emit_span can find it.
    run_ctx._trace_collector = trace  # type: ignore[attr-defined]

    return trace
