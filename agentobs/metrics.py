"""agentobs.metrics — Programmatic metrics extraction from AgentOBS traces.

Provides aggregation functions that accept any ``Iterable[Event]`` — such as
an in-memory list, an ``EventStream.from_file(...)`` iterator, or a
:class:`~agentobs._store.TraceStore` query result — and return structured
:class:`MetricsSummary` / :class:`LatencyStats` objects.

Usage::

    import agentobs.metrics as metrics
    from agentobs.stream import iter_file

    events = list(iter_file("events.jsonl"))
    summary = metrics.aggregate(events)
    print(f"Success rate: {summary.agent_success_rate:.1%}")
    print(f"p95 LLM latency: {summary.llm_latency_ms.p95:.1f} ms")
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from agentobs.event import Event
    from agentobs.namespaces.trace import TokenUsage

__all__ = [
    "LatencyStats",
    "MetricsSummary",
    "aggregate",
    "agent_success_rate",
    "llm_latency",
    "tool_failure_rate",
    "token_usage",
]

# ---------------------------------------------------------------------------
# EventType string constants (avoid circular import)
# ---------------------------------------------------------------------------

_SPAN_COMPLETED = "llm.trace.span.completed"
_SPAN_FAILED = "llm.trace.span.failed"
_AGENT_COMPLETED = "llm.trace.agent.completed"

_SPAN_EVENT_TYPES = frozenset({_SPAN_COMPLETED, _SPAN_FAILED})

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatencyStats:
    """Latency percentile distribution for LLM calls (all values in ms)."""

    min: float
    max: float
    p50: float
    p95: float
    p99: float

    @classmethod
    def _from_samples(cls, samples: list[float]) -> "LatencyStats":
        if not samples:
            return cls(min=0.0, max=0.0, p50=0.0, p95=0.0, p99=0.0)
        samples = sorted(samples)
        return cls(
            min=samples[0],
            max=samples[-1],
            p50=_percentile(samples, 50),
            p95=_percentile(samples, 95),
            p99=_percentile(samples, 99),
        )


@dataclass
class MetricsSummary:
    """Aggregated metrics extracted from a collection of AgentOBS events.

    Attributes:
        trace_count:           Number of distinct ``trace_id`` values seen.
        span_count:            Total number of span events.
        agent_success_rate:    Fraction of traces that contain no error spans
                               (0.0 – 1.0).
        avg_trace_duration_ms: Mean duration across all agent-run events.
        p50_trace_duration_ms: Median trace duration.
        p95_trace_duration_ms: 95th-percentile trace duration.
        total_input_tokens:    Cumulative input/prompt tokens across all spans.
        total_output_tokens:   Cumulative output/completion tokens across all spans.
        total_cost_usd:        Cumulative inferred cost in USD.
        llm_latency_ms:        :class:`LatencyStats` for LLM-type spans.
        tool_failure_rate:     Fraction of tool-call spans with ``status="error"``.
        token_usage_by_model:  Per-model ``TokenUsage``-like dict (input/output/total).
        cost_by_model:         Per-model total cost in USD.
    """

    trace_count: int = 0
    span_count: int = 0
    agent_success_rate: float = 1.0
    avg_trace_duration_ms: float = 0.0
    p50_trace_duration_ms: float = 0.0
    p95_trace_duration_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    llm_latency_ms: LatencyStats = field(default_factory=lambda: LatencyStats(0, 0, 0, 0, 0))
    tool_failure_rate: float = 0.0
    token_usage_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    cost_by_model: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(sorted_data: list[float], pct: float) -> float:
    """Return the *pct*-th percentile of an already-sorted list."""
    if not sorted_data:
        return 0.0
    if len(sorted_data) == 1:
        return sorted_data[0]
    idx = (pct / 100.0) * (len(sorted_data) - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_data):
        return float(sorted_data[-1])
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


def _event_type_str(event: "Event") -> str:
    """Return the string value of ``event.event_type``."""
    et = event.event_type
    return et.value if hasattr(et, "value") else str(et)


def _is_span_event(event: "Event") -> bool:
    return _event_type_str(event) in _SPAN_EVENT_TYPES


def _is_agent_completed(event: "Event") -> bool:
    return _event_type_str(event) == _AGENT_COMPLETED


def _is_llm_span(payload: dict) -> bool:
    op = payload.get("operation", "")
    return op in ("chat", "completion", "embedding", "chat_completion", "generate")


def _is_tool_span(payload: dict) -> bool:
    op = payload.get("operation", "")
    return op == "tool_call"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _process_llm_span(
    payload: dict[str, object],
    duration_ms: float,
    llm_latencies: list[float],
    token_by_model: dict[str, dict[str, int]],
    cost_by_model: dict[str, float],
) -> tuple[int, int, float]:
    """Process LLM span metrics; returns (input_tokens, output_tokens, cost_usd)."""
    if duration_ms >= 0:
        llm_latencies.append(duration_ms)
    inp = out = 0
    cost_usd = 0.0
    tu = payload.get("token_usage")
    if tu:
        inp = int(tu.get("input_tokens", 0))  # type: ignore[union-attr]
        out = int(tu.get("output_tokens", 0))  # type: ignore[union-attr]
        tot = int(tu.get("total_tokens", 0))   # type: ignore[union-attr]
        model_name = (payload.get("model") or {}).get("name", "unknown")  # type: ignore[union-attr]
        token_by_model[model_name]["input_tokens"] += inp
        token_by_model[model_name]["output_tokens"] += out
        token_by_model[model_name]["total_tokens"] += tot
    cost = payload.get("cost")
    if cost:
        cost_usd = float(cost.get("total_cost_usd", 0.0))  # type: ignore[union-attr]
        model_name = (payload.get("model") or {}).get("name", "unknown")  # type: ignore[union-attr]
        cost_by_model[model_name] += cost_usd
    return inp, out, cost_usd


def _process_span_event(
    event: "Event",
    span_count: int,
    trace_errors: dict[str, bool],
    llm_latencies: list[float],
    token_by_model: dict[str, dict[str, int]],
    cost_by_model: dict[str, float],
    tool_total: int,
    tool_errors: int,
    total_input_tokens: int,
    total_output_tokens: int,
    total_cost_usd: float,
) -> tuple[int, int, int, int, float]:
    """Process a single span event; returns updated counters."""
    payload = event.payload
    span_count += 1
    status = payload.get("status", "ok")
    trace_id = payload.get("trace_id", "")
    duration_ms = float(payload.get("duration_ms", 0.0))

    if trace_id and trace_id not in trace_errors:
        trace_errors[trace_id] = False  # type: ignore[assignment]

    if status == "error" and trace_id:
        trace_errors[trace_id] = True  # type: ignore[assignment]

    if _is_llm_span(payload):  # type: ignore[arg-type]
        inp, out, cost_usd = _process_llm_span(
            payload, duration_ms, llm_latencies, token_by_model, cost_by_model  # type: ignore[arg-type]
        )
        total_input_tokens += inp
        total_output_tokens += out
        total_cost_usd += cost_usd

    if _is_tool_span(payload):  # type: ignore[arg-type]
        tool_total += 1
        if status == "error":
            tool_errors += 1

    return span_count, tool_total, tool_errors, total_input_tokens, total_output_tokens, total_cost_usd  # type: ignore[return-value]


def aggregate(events: Iterable["Event"]) -> MetricsSummary:
    """Aggregate a collection of AgentOBS events into a :class:`MetricsSummary`.

    Args:
        events: Any iterable of :class:`~agentobs.event.Event` objects.

    Returns:
        A fully-populated :class:`MetricsSummary`.
    """
    events_list = list(events)

    # Track per-trace error status (trace_id → has_error)
    trace_errors: dict[str, bool] = {}
    trace_durations: list[float] = []

    span_count = 0
    llm_latencies: list[float] = []
    tool_total = 0
    tool_errors = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_usd = 0.0
    token_by_model: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    )
    cost_by_model: dict[str, float] = defaultdict(float)

    for event in events_list:
        payload = event.payload

        if _is_span_event(event):
            span_count, tool_total, tool_errors, total_input_tokens, total_output_tokens, total_cost_usd = _process_span_event(  # type: ignore[assignment]
                event, span_count, trace_errors, llm_latencies,
                token_by_model, cost_by_model,  # type: ignore[arg-type]
                tool_total, tool_errors, total_input_tokens,
                total_output_tokens, total_cost_usd,
            )

        elif _is_agent_completed(event):
            dur = float(payload.get("duration_ms", 0.0))
            trace_durations.append(dur)

    # Success rate
    if trace_errors:
        success_count = sum(1 for has_err in trace_errors.values() if not has_err)
        success_rate = success_count / len(trace_errors)
    else:
        success_rate = 1.0

    # Trace duration stats
    sorted_durations = sorted(trace_durations)
    avg_dur = statistics.mean(sorted_durations) if sorted_durations else 0.0
    p50_dur = _percentile(sorted_durations, 50)
    p95_dur = _percentile(sorted_durations, 95)

    return MetricsSummary(
        trace_count=len(trace_errors),
        span_count=span_count,
        agent_success_rate=success_rate,
        avg_trace_duration_ms=avg_dur,
        p50_trace_duration_ms=p50_dur,
        p95_trace_duration_ms=p95_dur,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cost_usd=total_cost_usd,
        llm_latency_ms=LatencyStats._from_samples(llm_latencies),
        tool_failure_rate=tool_errors / tool_total if tool_total > 0 else 0.0,
        token_usage_by_model=dict(token_by_model),
        cost_by_model=dict(cost_by_model),
    )


def agent_success_rate(events: Iterable["Event"]) -> float:
    """Return the fraction of traces with no error spans.

    Args:
        events: Any iterable of :class:`~agentobs.event.Event` objects.

    Returns:
        Success rate in the range 0.0 – 1.0.  Returns ``1.0`` when there are
        no span events (nothing to interpret as a failure).
    """
    return aggregate(events).agent_success_rate


def llm_latency(events: Iterable["Event"]) -> LatencyStats:
    """Return :class:`LatencyStats` for all LLM-operation spans.

    Args:
        events: Any iterable of :class:`~agentobs.event.Event` objects.

    Returns:
        Latency percentiles in milliseconds.
    """
    return aggregate(events).llm_latency_ms


def tool_failure_rate(events: Iterable["Event"]) -> float:
    """Return the fraction of tool-call spans that ended with ``status="error"``.

    Args:
        events: Any iterable of :class:`~agentobs.event.Event` objects.

    Returns:
        Failure rate in the range 0.0 – 1.0.
    """
    return aggregate(events).tool_failure_rate


def token_usage(events: Iterable["Event"]) -> dict[str, dict[str, int]]:
    """Return per-model token usage totals.

    Args:
        events: Any iterable of :class:`~agentobs.event.Event` objects.

    Returns:
        Dict mapping model name → ``{"input_tokens": int, "output_tokens": int,
        "total_tokens": int}``.
    """
    return aggregate(events).token_usage_by_model
