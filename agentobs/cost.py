"""agentobs.cost — Cost Calculation Engine (RFC-0001 §9, Tool 2).

Provides:
- :class:`CostRecord` — immutable record for a single LLM call cost entry.
- :class:`CostTracker` — accumulates ``CostRecord`` objects; computes aggregates.
- :class:`BudgetMonitor` — fires a callback when a cost threshold is exceeded.
- :func:`budget_alert` — convenience factory that registers a :class:`BudgetMonitor`
  against the global tracker.
- :func:`emit_cost_event` — builds a ``llm.cost.token.recorded`` event from a
  :class:`~agentobs._span.Span` and dispatches it through the active exporter.
- :func:`emit_cost_attributed` — emits a ``llm.cost.attributed`` event.
- :func:`cost_summary` — returns a plain-text table of cost data from a tracker.

Usage::

    from agentobs.cost import CostTracker, budget_alert

    tracker = CostTracker()
    budget_alert(0.50, on_exceeded=lambda t: print(f"Budget hit! ${t.total_usd:.4f}"),
                 tracker=tracker)

    tracker.record("gpt-4o", input_tokens=500, output_tokens=200)
    tracker.record("gpt-4o-mini", input_tokens=1000, output_tokens=400)
    print(cost_summary(tracker))
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agentobs._span import Span

__all__ = [
    "BudgetMonitor",
    "CostRecord",
    "CostTracker",
    "budget_alert",
    "cost_summary",
    "emit_cost_attributed",
    "emit_cost_event",
]

# ---------------------------------------------------------------------------
# Module-level default tracker (used by budget_alert() when tracker=None)
# ---------------------------------------------------------------------------

_global_tracker_lock = threading.Lock()
_global_tracker: "CostTracker | None" = None


def _get_global_tracker() -> "CostTracker":
    global _global_tracker  # noqa: PLW0603
    if _global_tracker is None:
        with _global_tracker_lock:
            if _global_tracker is None:
                _global_tracker = CostTracker()
    return _global_tracker


# ---------------------------------------------------------------------------
# CostRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostRecord:
    """Immutable record for a single LLM call cost entry.

    Attributes:
        model:           Model name (e.g. ``"gpt-4o"``).
        input_tokens:    Number of input/prompt tokens consumed.
        output_tokens:   Number of output/completion tokens generated.
        total_usd:       Total cost in USD for this call.
        input_cost_usd:  Cost for input tokens alone (None if unknown).
        output_cost_usd: Cost for output tokens alone (None if unknown).
        tags:            Arbitrary string key-value metadata.
        span_id:         ID of the originating span, if any.
        agent_run_id:    ULID of the enclosing agent run, if any.
        timestamp:       Unix timestamp (seconds) when the record was created.
    """

    model: str
    input_tokens: int
    output_tokens: int
    total_usd: float
    input_cost_usd: float | None = None
    output_cost_usd: float | None = None
    tags: dict[str, str] = field(default_factory=dict)
    span_id: str | None = None
    agent_run_id: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_usd": self.total_usd,
            "timestamp": self.timestamp,
        }
        if self.input_cost_usd is not None:
            d["input_cost_usd"] = self.input_cost_usd
        if self.output_cost_usd is not None:
            d["output_cost_usd"] = self.output_cost_usd
        if self.tags:
            d["tags"] = dict(self.tags)
        if self.span_id is not None:
            d["span_id"] = self.span_id
        if self.agent_run_id is not None:
            d["agent_run_id"] = self.agent_run_id
        return d


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Accumulates :class:`CostRecord` entries and exposes aggregates.

    Thread-safe: all mutations and reads are protected by an internal lock.

    Usage::

        tracker = CostTracker()
        tracker.record("gpt-4o", input_tokens=500, output_tokens=200)
        print(f"Total: ${tracker.total_usd:.6f}")
        print(tracker.breakdown_by_model)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[CostRecord] = []
        self._monitors: list[BudgetMonitor] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        total_usd: float | None = None,
        input_cost_usd: float | None = None,
        output_cost_usd: float | None = None,
        tags: dict[str, str] | None = None,
        span_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> CostRecord:
        """Record a single LLM call cost.

        If *total_usd* is not provided, the cost is calculated from the
        ``agentobs.integrations._pricing`` table.  If the model is not in the
        table, ``total_usd`` defaults to ``0.0``.

        Args:
            model:          Model name (e.g. ``"gpt-4o"``).
            input_tokens:   Input/prompt token count.
            output_tokens:  Output/completion token count.
            total_usd:      Override total cost in USD (skips pricing lookup).
            input_cost_usd: Override just the input cost (optional).
            output_cost_usd: Override just the output cost (optional).
            tags:           Arbitrary string metadata for grouping.
            span_id:        ID of the originating span.
            agent_run_id:   ULID of the enclosing agent run.

        Returns:
            The created :class:`CostRecord`.
        """
        if not isinstance(model, str) or not model:
            raise ValueError("CostTracker.record: model must be a non-empty string")
        if not isinstance(input_tokens, int) or input_tokens < 0:
            raise ValueError("CostTracker.record: input_tokens must be a non-negative int")
        if not isinstance(output_tokens, int) or output_tokens < 0:
            raise ValueError("CostTracker.record: output_tokens must be a non-negative int")

        if total_usd is None:
            input_cost_usd, output_cost_usd, total_usd = _calculate_cost(
                model, input_tokens, output_tokens
            )
        elif input_cost_usd is None and output_cost_usd is None:
            # No breakdown provided — leave both as None
            pass

        cr = CostRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_usd=total_usd,
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            tags=dict(tags) if tags else {},
            span_id=span_id,
            agent_run_id=agent_run_id,
        )

        with self._lock:
            self._records.append(cr)

        # Check budget monitors outside the lock to avoid re-entrant deadlock.
        self._check_monitors()

        return cr

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------

    @property
    def total_usd(self) -> float:
        """Total cost in USD across all recorded calls."""
        with self._lock:
            return sum(r.total_usd for r in self._records)

    @property
    def call_count(self) -> int:
        """Number of recorded calls."""
        with self._lock:
            return len(self._records)

    @property
    def total_input_tokens(self) -> int:
        """Total input tokens across all recorded calls."""
        with self._lock:
            return sum(r.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        """Total output tokens across all recorded calls."""
        with self._lock:
            return sum(r.output_tokens for r in self._records)

    @property
    def breakdown_by_model(self) -> dict[str, float]:
        """Per-model total cost in USD, sorted by descending cost."""
        totals: dict[str, float] = {}
        with self._lock:
            for r in self._records:
                totals[r.model] = totals.get(r.model, 0.0) + r.total_usd
        return dict(sorted(totals.items(), key=lambda kv: kv[1], reverse=True))

    @property
    def breakdown_by_tag(self) -> dict[str, dict[str, float]]:
        """Per-tag-key/value total cost.

        Returns ``{tag_key: {tag_value: total_usd, ...}, ...}``.
        Only tags present on at least one record are included.
        """
        result: dict[str, dict[str, float]] = {}
        with self._lock:
            for r in self._records:
                for k, v in r.tags.items():
                    if k not in result:
                        result[k] = {}
                    result[k][v] = result[k].get(v, 0.0) + r.total_usd
        return result

    @property
    def records(self) -> list[CostRecord]:
        """Return a snapshot of all recorded :class:`CostRecord` objects."""
        with self._lock:
            return list(self._records)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all recorded cost data and reset per-monitor fired state."""
        with self._lock:
            self._records.clear()
            for monitor in self._monitors:
                monitor._fired = False

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the tracker state to a plain dict."""
        with self._lock:
            records = list(self._records)
        return {
            "total_usd": sum(r.total_usd for r in records),
            "call_count": len(records),
            "total_input_tokens": sum(r.input_tokens for r in records),
            "total_output_tokens": sum(r.output_tokens for r in records),
            "breakdown_by_model": {
                m: sum(r.total_usd for r in records if r.model == m)
                for m in {r.model for r in records}
            },
            "records": [r.to_dict() for r in records],
        }

    # ------------------------------------------------------------------
    # Internal monitor management
    # ------------------------------------------------------------------

    def _add_monitor(self, monitor: "BudgetMonitor") -> None:
        with self._lock:
            self._monitors.append(monitor)

    def _check_monitors(self) -> None:
        """Fire any monitors whose threshold has been exceeded."""
        with self._lock:
            monitors = list(self._monitors)
        # Check outside the lock — callbacks may call back into the tracker.
        for monitor in monitors:
            monitor.check(self)


# ---------------------------------------------------------------------------
# BudgetMonitor
# ---------------------------------------------------------------------------


class BudgetMonitor:
    """Fires a callback when a :class:`CostTracker` exceeds a USD threshold.

    The callback is invoked **at most once** per budget period (unless the
    tracker is :meth:`~CostTracker.reset`-ed, which resets the fired state).

    Args:
        threshold_usd: USD threshold that triggers the alert.
        on_exceeded:   Callable ``(CostTracker) -> None`` invoked on breach.

    Usage::

        monitor = BudgetMonitor(
            threshold_usd=1.00,
            on_exceeded=lambda t: print(f"Over budget: ${t.total_usd:.4f}")
        )
        tracker = CostTracker()
        tracker._add_monitor(monitor)
    """

    def __init__(
        self,
        threshold_usd: float,
        on_exceeded: Callable[["CostTracker"], None],
    ) -> None:
        if threshold_usd <= 0:
            raise ValueError("BudgetMonitor: threshold_usd must be > 0")
        if not callable(on_exceeded):
            raise TypeError("BudgetMonitor: on_exceeded must be callable")
        self.threshold_usd = threshold_usd
        self.on_exceeded = on_exceeded
        self._fired = False

    def check(self, tracker: CostTracker) -> bool:
        """Check whether the tracker exceeds the threshold and fire if so.

        Fires **at most once** per tracker lifetime (until :meth:`~CostTracker.reset`
        is called).

        Args:
            tracker: The :class:`CostTracker` to check against.

        Returns:
            ``True`` if the callback was fired on this call, ``False`` otherwise.
        """
        if self._fired:
            return False
        if tracker.total_usd >= self.threshold_usd:
            self._fired = True
            try:
                self.on_exceeded(tracker)
            except Exception:  # NOSONAR — never let a callback kill the recording path
                pass
            return True
        return False


# ---------------------------------------------------------------------------
# budget_alert() factory
# ---------------------------------------------------------------------------


def budget_alert(
    threshold_usd: float,
    on_exceeded: Callable[["CostTracker"], None],
    *,
    tracker: CostTracker | None = None,
) -> BudgetMonitor:
    """Register a :class:`BudgetMonitor` on *tracker* (or the global default).

    Creates a new :class:`BudgetMonitor` and attaches it to *tracker*.  If
    *tracker* is ``None`` the module-level global tracker is used.

    Args:
        threshold_usd: USD amount that triggers *on_exceeded*.
        on_exceeded:   Callback ``(CostTracker) -> None`` fired on breach.
        tracker:       Tracker to monitor.  Defaults to the global tracker.

    Returns:
        The created :class:`BudgetMonitor`.
    """
    t = tracker if tracker is not None else _get_global_tracker()
    monitor = BudgetMonitor(threshold_usd=threshold_usd, on_exceeded=on_exceeded)
    t._add_monitor(monitor)
    return monitor


# ---------------------------------------------------------------------------
# Cost calculation helper
# ---------------------------------------------------------------------------


def _calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> tuple[float, float, float]:
    """Return ``(input_cost_usd, output_cost_usd, total_usd)`` for *model*.

    Uses the static pricing table in ``agentobs.integrations._pricing``.
    Returns ``(0.0, 0.0, 0.0)`` when the model is not found in the table.
    """
    try:
        from agentobs.integrations._pricing import get_pricing  # noqa: PLC0415
        pricing = get_pricing(model)
    except Exception:  # NOSONAR
        pricing = None

    if pricing is None:
        return (0.0, 0.0, 0.0)

    # Pricing table is USD per *million* tokens.
    input_rate = pricing.get("input", 0.0)
    output_rate = pricing.get("output", 0.0)
    input_cost = (input_tokens / 1_000_000.0) * input_rate
    output_cost = (output_tokens / 1_000_000.0) * output_rate
    return (input_cost, output_cost, input_cost + output_cost)


# ---------------------------------------------------------------------------
# Event emission helpers
# ---------------------------------------------------------------------------


def emit_cost_event(
    span: "Span",
    *,
    token_usage: Any = None,
    model_info: Any = None,
) -> None:
    """Emit a ``llm.cost.token.recorded`` event for *span*.

    The span MUST have a ``cost`` attribute (``CostBreakdown``).  If
    *token_usage* or *model_info* are not provided they are read from
    ``span.token_usage`` and resolved from ``span.model`` respectively.

    This function is a no-op when ``span.cost`` is ``None``.

    Args:
        span:        The finished :class:`~agentobs._span.Span`.
        token_usage: Override the :class:`~agentobs.namespaces.trace.TokenUsage`.
        model_info:  Override the :class:`~agentobs.namespaces.trace.ModelInfo`.
    """
    from agentobs._span import Span, _resolve_model_info  # noqa: PLC0415
    from agentobs._stream import _build_event, _dispatch  # noqa: PLC0415
    from agentobs.namespaces.cost import CostTokenRecordedPayload  # noqa: PLC0415
    from agentobs.namespaces.trace import ModelInfo, TokenUsage  # noqa: PLC0415
    from agentobs.types import EventType  # noqa: PLC0415

    assert isinstance(span, Span)
    if span.cost is None:
        return

    # Resolve token_usage
    if token_usage is None:
        token_usage = span.token_usage
    if token_usage is None:
        # Build a minimal TokenUsage so the payload is always valid.
        token_usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

    # Resolve model_info
    if model_info is None:
        if span.model:
            model_info = _resolve_model_info(span.model)
        else:
            model_info = ModelInfo(system="openai", name="unknown")

    payload = CostTokenRecordedPayload(
        cost=span.cost,
        token_usage=token_usage,
        model=model_info,
        span_id=span.span_id,
        agent_run_id=span.agent_run_id,
    )
    event = _build_event(
        event_type=EventType.COST_TOKEN_RECORDED,
        payload_dict=payload.to_dict(),
        span_id=span.span_id,
        trace_id=span.trace_id,
        parent_span_id=span.parent_span_id,
    )
    _dispatch(event)


def emit_cost_attributed(
    attribution_target: str,
    total_usd: float,
    attribution_type: str = "direct",
    *,
    source_event_ids: list[str] | None = None,
    pricing_date: str | None = None,
) -> None:
    """Emit a ``llm.cost.attributed`` event.

    Args:
        attribution_target: Identifier for the attribution target
                            (e.g. org/team/user/env).
        total_usd:          Total cost to attribute in USD.
        attribution_type:   One of ``"direct"``, ``"proportional"``,
                            ``"estimated"``, ``"manual"``.
        source_event_ids:   Optional list of source event IDs.
        pricing_date:       ISO date string for reproducible cost calculation.
    """
    from agentobs._stream import _build_event, _dispatch  # noqa: PLC0415
    from agentobs.namespaces.cost import CostAttributedPayload  # noqa: PLC0415
    from agentobs.namespaces.trace import CostBreakdown  # noqa: PLC0415
    from agentobs.types import EventType  # noqa: PLC0415

    cost = CostBreakdown(
        input_cost_usd=total_usd,
        output_cost_usd=0.0,
        total_cost_usd=total_usd,
        pricing_date=pricing_date or "2026-01-01",
    )
    payload = CostAttributedPayload(
        cost=cost,
        attribution_target=attribution_target,
        attribution_type=attribution_type,
        source_event_ids=list(source_event_ids) if source_event_ids else [],
    )
    event = _build_event(
        event_type=EventType.COST_ATTRIBUTED,
        payload_dict=payload.to_dict(),
    )
    _dispatch(event)


# ---------------------------------------------------------------------------
# cost_summary() — terminal display helper
# ---------------------------------------------------------------------------


def cost_summary(tracker: CostTracker | None = None) -> str:
    """Return a plain-text table of cost data from *tracker*.

    Uses the global tracker if *tracker* is ``None``.

    Args:
        tracker: :class:`CostTracker` to summarise.

    Returns:
        A multi-line string table suitable for ``print()``.
    """
    t = tracker if tracker is not None else _get_global_tracker()

    lines: list[str] = []
    lines.append("=" * 54)
    lines.append(f"  AgentOBS Cost Summary")
    lines.append("=" * 54)
    lines.append(f"  Total calls        : {t.call_count}")
    lines.append(f"  Total input tokens : {t.total_input_tokens:,}")
    lines.append(f"  Total output tokens: {t.total_output_tokens:,}")
    lines.append(f"  Total cost (USD)   : ${t.total_usd:.6f}")
    lines.append("-" * 54)

    breakdown = t.breakdown_by_model
    if breakdown:
        lines.append("  Cost by model:")
        for model, cost in breakdown.items():
            lines.append(f"    {model:<38} ${cost:.6f}")
    else:
        lines.append("  No calls recorded.")

    tag_breakdown = t.breakdown_by_tag
    if tag_breakdown:
        lines.append("-" * 54)
        lines.append("  Cost by tag:")
        for tag_key, tag_values in tag_breakdown.items():
            for tag_val, cost in sorted(tag_values.items(), key=lambda kv: kv[1], reverse=True):
                lines.append(f"    [{tag_key}={tag_val}]{'':>24} ${cost:.6f}")

    lines.append("=" * 54)
    return "\n".join(lines)
