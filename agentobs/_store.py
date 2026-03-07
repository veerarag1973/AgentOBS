"""agentobs._store — In-process ring-buffer trace store.

Retains the last *N* traces in memory for programmatic querying via
:func:`~agentobs.get_trace`, :func:`~agentobs.get_last_agent_run`, etc.

The store is opt-in (disabled by default) to keep memory overhead zero for
users who do not need it.  Enable via::

    from agentobs import configure
    configure(enable_trace_store=True, trace_store_size=200)

or environment variable ``AGENTOBS_ENABLE_TRACE_STORE=1``.

Security
--------
Events are stored **after** the redaction pass in :func:`~agentobs._stream._dispatch`.
When a :class:`~agentobs.redact.RedactionPolicy` is configured, all PII has
been masked before an event reaches :meth:`TraceStore.record`.  The store
never bypasses redaction.

The ring buffer is bounded to ``trace_store_size`` *traces* (not individual
events).  Once full, the oldest trace is evicted to make room for the new one.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentobs.event import Event
    from agentobs.namespaces.trace import SpanPayload

__all__ = ["TraceStore", "get_store"]

# ---------------------------------------------------------------------------
# EventType string constants (avoid circular import)
# ---------------------------------------------------------------------------

_SPAN_COMPLETED = "llm.trace.span.completed"
_SPAN_FAILED = "llm.trace.span.failed"
_AGENT_COMPLETED = "llm.trace.agent.completed"
_SPAN_EVENT_TYPES = frozenset({_SPAN_COMPLETED, _SPAN_FAILED})


def _event_type_str(event: "Event") -> str:
    et = event.event_type
    return et.value if hasattr(et, "value") else str(et)


# ---------------------------------------------------------------------------
# TraceStore
# ---------------------------------------------------------------------------


class TraceStore:
    """Thread-safe in-memory ring buffer storing the last *max_traces* traces.

    Each trace is keyed by its ``trace_id``; events without a ``trace_id`` are
    stored under the sentinel key ``"__no_trace_id__"``.

    Args:
        max_traces: Maximum number of distinct traces to retain.  Oldest trace
                    is evicted when the buffer is full.  Default: 100.
    """

    def __init__(self, max_traces: int = 100) -> None:
        if max_traces < 1:
            raise ValueError("TraceStore.max_traces must be >= 1")
        self._max_traces = max_traces
        # OrderedDict preserves insertion order; oldest = first.
        self._traces: OrderedDict[str, list["Event"]] = OrderedDict()
        self._last_agent_trace_id: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_trace_id(self, event: "Event") -> str:
        """Extract the trace_id from the event payload or envelope."""
        tid = getattr(event, "trace_id", None) or event.payload.get("trace_id", "")
        return str(tid) if tid else "__no_trace_id__"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def record(self, event: "Event") -> None:
        """Append *event* to the store.

        Evicts the oldest trace when the buffer would exceed ``max_traces``.

        Args:
            event: A fully-formed (and already-redacted) :class:`~agentobs.event.Event`.
        """
        trace_id = self._resolve_trace_id(event)
        with self._lock:
            if trace_id not in self._traces:
                # Evict oldest if full.
                if len(self._traces) >= self._max_traces:
                    self._traces.popitem(last=False)
                self._traces[trace_id] = []
            else:
                # Move to end (most recently active).
                self._traces.move_to_end(trace_id)
            self._traces[trace_id].append(event)

            # Track the most recently completed agent run.
            if _event_type_str(event) == _AGENT_COMPLETED:
                self._last_agent_trace_id = trace_id

    def get_trace(self, trace_id: str) -> list["Event"] | None:
        """Return all stored events for *trace_id*, or ``None`` if not found.

        Args:
            trace_id: The 32-character hex trace identifier.

        Returns:
            A copy of the event list so callers cannot mutate the store's
            internal state.
        """
        with self._lock:
            events = self._traces.get(trace_id)
            return list(events) if events is not None else None

    def get_last_agent_run(self) -> list["Event"] | None:
        """Return all events for the most recently completed agent-run trace.

        Returns:
            A copy of the event list, or ``None`` if no agent run has been
            recorded yet.
        """
        with self._lock:
            if self._last_agent_trace_id is None:
                return None
            events = self._traces.get(self._last_agent_trace_id)
            return list(events) if events is not None else None

    def list_tool_calls(self, trace_id: str) -> list["SpanPayload"]:
        """Return deserialized :class:`~agentobs.namespaces.trace.SpanPayload` objects
        for every tool-call span in *trace_id*.

        Args:
            trace_id: The 32-character hex trace identifier.

        Returns:
            List of ``SpanPayload`` objects for tool-call spans, sorted by
            ``start_time_unix_nano``.  Returns an empty list if the trace is
            not found or contains no tool-call spans.
        """
        return self._list_spans_by_operation(trace_id, "tool_call")

    def list_llm_calls(self, trace_id: str) -> list["SpanPayload"]:
        """Return deserialized :class:`~agentobs.namespaces.trace.SpanPayload` objects
        for every LLM-operation span in *trace_id*.

        Args:
            trace_id: The 32-character hex trace identifier.

        Returns:
            List of ``SpanPayload`` objects for LLM spans, sorted by
            ``start_time_unix_nano``.
        """
        llm_ops = frozenset({"chat", "completion", "embedding", "chat_completion", "generate"})
        return self._list_spans_by_operation(trace_id, *llm_ops)

    def _list_spans_by_operation(self, trace_id: str, *operations: str) -> list["SpanPayload"]:
        """Shared implementation for list_tool_calls / list_llm_calls."""
        from agentobs.namespaces.trace import SpanPayload  # noqa: PLC0415

        with self._lock:
            events = self._traces.get(trace_id)
            if not events:
                return []
            result: list[SpanPayload] = []
            for event in events:
                if _event_type_str(event) not in _SPAN_EVENT_TYPES:
                    continue
                payload = event.payload
                op = payload.get("operation", "")
                if op in operations:
                    try:
                        result.append(SpanPayload.from_dict(payload))
                    except Exception:
                        pass  # malformed span — skip without raising
            result.sort(key=lambda s: s.start_time_unix_nano)
            return result

    def clear(self) -> None:
        """Remove all stored traces and reset the last-agent-run pointer."""
        with self._lock:
            self._traces.clear()
            self._last_agent_trace_id = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._traces)

    def __repr__(self) -> str:
        with self._lock:
            return f"TraceStore(traces={len(self._traces)}, max={self._max_traces})"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: TraceStore = TraceStore()


def get_store() -> TraceStore:
    """Return the module-level :class:`TraceStore` singleton.

    The singleton is recreated whenever
    :func:`~agentobs._stream._reset_exporter` is called (e.g. after
    ``configure(trace_store_size=…)``).
    """
    return _store


def _reset_store(max_traces: int = 100) -> None:
    """Recreate the module-level store with a new *max_traces* limit.

    Called by :func:`~agentobs._stream._reset_exporter` after ``configure()``
    so that a changed ``trace_store_size`` takes effect immediately.
    """
    global _store  # noqa: PLW0603
    _store = TraceStore(max_traces=max_traces)


# ---------------------------------------------------------------------------
# Convenience module-level access functions (re-exported via __init__.py)
# ---------------------------------------------------------------------------


def get_trace(trace_id: str) -> list["Event"] | None:
    """Return all stored events for *trace_id*.  See :meth:`TraceStore.get_trace`."""
    return get_store().get_trace(trace_id)


def get_last_agent_run() -> list["Event"] | None:
    """Return events for the most recent agent-run trace.  See :meth:`TraceStore.get_last_agent_run`."""
    return get_store().get_last_agent_run()


def list_tool_calls(trace_id: str) -> list["SpanPayload"]:
    """Return tool-call spans for *trace_id*.  See :meth:`TraceStore.list_tool_calls`."""
    return get_store().list_tool_calls(trace_id)


def list_llm_calls(trace_id: str) -> list["SpanPayload"]:
    """Return LLM-call spans for *trace_id*.  See :meth:`TraceStore.list_llm_calls`."""
    return get_store().list_llm_calls(trace_id)
