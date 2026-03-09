"""agentobs.inspect — Tool Call Inspector (RFC-0001, Tool 3 / llm-inspect).

Surfaces every tool call in an agent run: function name, arguments, return
value, execution time, and whether the model actually used the tool's output.

Public API::

    from agentobs.inspect import InspectorSession, inspect_trace

    # --- Runtime inspection ---
    session = InspectorSession()
    tracer = agentobs.Tracer()
    with tracer.agent_run("research") as run:
        session.attach(run)       # start recording tool spans
        result = my_tool("query")
    session.detach()              # stop recording

    for call in session.tool_calls:
        print(call.name, call.duration_ms, call.was_result_used)
    print(session.summary())

    # --- Post-run replay from JSONL ---
    calls = inspect_trace("events.jsonl", trace_id="01XXXX")
    for call in calls:
        print(call)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agentobs._span import AgentRunContext, Span

__all__ = [
    "InspectorSession",
    "ToolCallRecord",
    "inspect_trace",
]

# ---------------------------------------------------------------------------
# ToolCallRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCallRecord:
    """Immutable record capturing one tool function invocation.

    Attributes:
        name:             Function name (span name).
        args:             Captured argument dict ``{param_name: repr_str}``.
                          Populated when ``@trace(tool=True, capture_args=True)``
                          or when ``@trace(tool=True)`` (args captured automatically).
        result:           Captured return value repr string.  ``None`` if
                          return capture was not enabled.
        duration_ms:      Wall-clock duration in milliseconds, or ``None`` if
                          the span did not record a duration.
        span_id:          OTel-compatible 16-char hex span ID.
        trace_id:         OTel-compatible 32-char hex trace ID.
        timestamp:        Unix timestamp (seconds) when the tool call started.
        status:           Span status: ``"ok"``, ``"error"``, or ``"timeout"``.
        error:            Error message if ``status == "error"``, else ``None``.
        was_result_used:  Heuristic result:
                          ``True``  — tool result string was found in a
                          subsequent span's captured arguments (likely used).
                          ``False`` — no subsequent span contained the result
                          (likely discarded).
                          ``None``  — indeterminate (no result captured, or no
                          subsequent spans).
    """

    name: str
    args: dict[str, Any]
    result: Any
    duration_ms: float | None
    span_id: str
    trace_id: str
    timestamp: float
    status: str
    error: str | None
    was_result_used: bool | None = None

    def __str__(self) -> str:
        dur = f"{self.duration_ms:.1f}ms" if self.duration_ms is not None else "?"
        used_str = {True: "used", False: "discarded", None: "unknown"}[self.was_result_used]
        err_part = f"  error={self.error!r}" if self.error else ""
        return (
            f"ToolCallRecord(name={self.name!r}, duration={dur}, "
            f"status={self.status!r}, result_used={used_str}{err_part})"
        )


# ---------------------------------------------------------------------------
# InspectorSession
# ---------------------------------------------------------------------------

_TOOL_OPERATIONS = frozenset({"execute_tool", "tool_call"})


def _is_tool_span(span: "Span") -> bool:
    """Return True if *span* represents a tool call."""
    op = str(getattr(span, "operation", "") or "")
    if op in _TOOL_OPERATIONS:
        return True
    attrs = getattr(span, "attributes", {}) or {}
    return bool(attrs.get("tool"))


def _extract_args(span: "Span") -> dict[str, Any]:
    """Extract ``arg.*`` attributes from *span* into a plain dict."""
    attrs = getattr(span, "attributes", {}) or {}
    return {k[4:]: v for k, v in attrs.items() if k.startswith("arg.")}


def _extract_result(span: "Span") -> Any:
    """Return the ``return_value`` attribute of *span*, or ``None``."""
    attrs = getattr(span, "attributes", {}) or {}
    return attrs.get("return_value")


def _check_result_used(tool_span: "Span", subsequent_spans: list["Span"]) -> bool | None:
    """Heuristic: did any subsequent span capture the tool result in its args?

    Scans the ``arg.*`` attributes of every subsequent span for the tool
    result string.  Returns ``True`` if found, ``False`` if not found,
    or ``None`` if the result was not captured or subsequent spans are absent.
    """
    result = _extract_result(tool_span)
    if result is None:
        return None
    result_str = str(result)
    # Skip trivially empty or un-informative results.
    if not result_str or result_str in ("None", "<unrepresentable>", "''", '""'):
        return None
    if not subsequent_spans:
        return None

    for span in subsequent_spans:
        attrs = getattr(span, "attributes", {}) or {}
        for v in attrs.values():
            if isinstance(v, str) and result_str in v:
                return True
    return False


class InspectorSession:
    """Collects tool call records from live span events.

    Attach to an :class:`~agentobs._span.AgentRunContext` (or globally) to
    intercept every span that closes with ``operation="execute_tool"`` or
    ``attributes["tool"] = True``.

    Usage::

        session = InspectorSession()
        with tracer.agent_run("research") as run:
            session.attach(run)
            result = search("query")   # @trace(tool=True)
        session.detach()

        for call in session.tool_calls:
            print(call)

        print(session.summary())

    The session is *not* reusable: call :meth:`reset` if you want to start a
    fresh recording on the same instance.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: list["Span"] = []      # all spans captured (tool + model)
        self._active = False
        self._trace_id_filter: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def attach(self, run: "AgentRunContext | None" = None) -> "InspectorSession":
        """Start recording tool call spans.

        Args:
            run: Optional :class:`~agentobs._span.AgentRunContext` returned
                 by ``tracer.agent_run()``.  When provided, only spans that
                 belong to this run's ``trace_id`` are recorded.  When
                 ``None``, all spans are captured globally.

        Returns:
            ``self`` for chaining.
        """
        self._active = True
        if run is not None:
            self._trace_id_filter = getattr(run, "trace_id", None)

        from agentobs._hooks import hooks  # noqa: PLC0415
        hooks.on_span_end(self._on_span_end)
        return self

    def detach(self) -> "InspectorSession":
        """Stop recording new spans.

        The hook remains registered in the global registry but is a no-op
        once ``_active`` is ``False``.  Call :meth:`reset` to clear recorded
        data.

        Returns:
            ``self`` for chaining.
        """
        self._active = False
        return self

    def reset(self) -> "InspectorSession":
        """Clear all recorded spans and re-enable recording.

        Returns:
            ``self`` for chaining.
        """
        with self._lock:
            self._spans.clear()
        self._active = True
        self._trace_id_filter = None
        return self

    # ------------------------------------------------------------------
    # Hook callback
    # ------------------------------------------------------------------

    def _on_span_end(self, span: "Span") -> None:
        if not self._active:
            return
        trace_id = getattr(span, "trace_id", None)
        if self._trace_id_filter and trace_id != self._trace_id_filter:
            return
        with self._lock:
            self._spans.append(span)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def tool_calls(self) -> list[ToolCallRecord]:
        """Return tool call records with heuristic ``was_result_used`` flags.

        Records are returned in the order the spans were collected (typically
        chronological).  ``was_result_used`` is computed lazily by scanning
        all spans captured after each tool span.
        """
        with self._lock:
            spans = list(self._spans)

        records: list[ToolCallRecord] = []
        for i, span in enumerate(spans):
            if not _is_tool_span(span):
                continue
            subsequent = spans[i + 1:]
            was_used = _check_result_used(span, subsequent)
            records.append(
                ToolCallRecord(
                    name=getattr(span, "name", ""),
                    args=_extract_args(span),
                    result=_extract_result(span),
                    duration_ms=getattr(span, "duration_ms", None),
                    span_id=getattr(span, "span_id", ""),
                    trace_id=getattr(span, "trace_id", ""),
                    timestamp=getattr(span, "start_ns", 0) / 1_000_000_000.0,
                    status=getattr(span, "status", "ok"),
                    error=getattr(span, "error", None),
                    was_result_used=was_used,
                )
            )
        return records

    @property
    def all_span_count(self) -> int:
        """Total number of spans captured (tool + non-tool)."""
        with self._lock:
            return len(self._spans)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a plain-text table of all recorded tool calls.

        Returns:
            Multi-line string suitable for ``print()``.
        """
        calls = self.tool_calls
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("  AgentOBS Tool Call Inspector")
        lines.append("=" * 72)
        if not calls:
            lines.append("  No tool calls recorded.")
            lines.append("=" * 72)
            return "\n".join(lines)

        lines.append(
            f"  {'Name':<28} {'Duration':>10}  {'Status':<8}  {'Result Used':<12}"
        )
        lines.append("-" * 72)
        for r in calls:
            dur = f"{r.duration_ms:.1f}ms" if r.duration_ms is not None else "?"
            used = {True: "yes", False: "no", None: "?"}[r.was_result_used]
            lines.append(
                f"  {r.name:<28} {dur:>10}  {r.status:<8}  {used:<12}"
            )
            if r.error:
                lines.append(f"    error: {r.error}")
        lines.append("=" * 72)
        lines.append(f"  Total: {len(calls)} tool call(s)")
        lines.append("=" * 72)
        return "\n".join(lines)

    def __repr__(self) -> str:
        return self.summary()

    def __len__(self) -> int:
        return len(self.tool_calls)


# ---------------------------------------------------------------------------
# inspect_trace() — JSONL replay
# ---------------------------------------------------------------------------


def inspect_trace(
    path: str,
    *,
    trace_id: str | None = None,
    skip_errors: bool = False,
) -> list[ToolCallRecord]:
    """Reconstruct tool call records from a JSONL trace file.

    Reads every span event from *path*, filters to tool spans (those with
    ``operation="execute_tool"`` or ``attributes.tool=true``), and returns
    a list of :class:`ToolCallRecord` objects.  The ``was_result_used``
    heuristic is applied against all other span events in the same file.

    Args:
        path:         Path to the NDJSON/JSONL events file.
        trace_id:     When provided, only records whose ``trace_id`` matches
                      are returned.  ``None`` returns records from all traces.
        skip_errors:  When ``True``, malformed JSONL lines are silently
                      skipped instead of raising.

    Returns:
        Ordered list of :class:`ToolCallRecord` objects.

    Raises:
        DeserializationError: On the first malformed line when
            ``skip_errors=False``.
    """
    from agentobs.stream import iter_file  # noqa: PLC0415

    _SPAN_EVENTS = frozenset({
        "llm.trace.span.completed",
        "llm.trace.span.failed",
    })

    # Collect all span payloads (and their index for ordering).
    all_payloads: list[dict] = []

    for event in iter_file(path, skip_errors=skip_errors):
        et = event.event_type
        et_str = et.value if hasattr(et, "value") else str(et)
        if et_str not in _SPAN_EVENTS:
            continue
        payload = event.payload
        if trace_id:
            if payload.get("trace_id") != trace_id:
                continue
        all_payloads.append(payload)

    # Identify tool span indices.
    records: list[ToolCallRecord] = []
    for i, payload in enumerate(all_payloads):
        op = payload.get("operation", "")
        attrs: dict = payload.get("attributes") or {}
        is_tool = op in _TOOL_OPERATIONS or bool(attrs.get("tool"))
        if not is_tool:
            continue

        subsequent = all_payloads[i + 1:]
        result = attrs.get("return_value")
        was_used = _check_result_used_from_dicts(result, subsequent)

        start_ns = payload.get("start_time_unix_nano") or 0
        records.append(
            ToolCallRecord(
                name=payload.get("span_name", ""),
                args={k[4:]: v for k, v in attrs.items() if k.startswith("arg.")},
                result=result,
                duration_ms=payload.get("duration_ms"),
                span_id=payload.get("span_id") or "",
                trace_id=payload.get("trace_id") or "",
                timestamp=start_ns / 1_000_000_000.0,
                status=payload.get("status", "ok"),
                error=payload.get("error"),
                was_result_used=was_used,
            )
        )
    return records


def _check_result_used_from_dicts(
    result: Any,
    subsequent_payloads: list[dict],
) -> bool | None:
    """Dict-based variant of the heuristic used by :func:`inspect_trace`."""
    if result is None:
        return None
    result_str = str(result)
    if not result_str or result_str in ("None", "<unrepresentable>", "''", '""'):
        return None
    if not subsequent_payloads:
        return None
    for payload in subsequent_payloads:
        sp_attrs: dict = payload.get("attributes") or {}
        for v in sp_attrs.values():
            if isinstance(v, str) and result_str in v:
                return True
    return False
