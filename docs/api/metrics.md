# agentobs.metrics

Batch aggregation API for computing structured metrics from collections of
AgentOBS events.

---

## `aggregate`

```python
def aggregate(events: Iterable[Event]) -> MetricsSummary
```

Compute a fully-populated `MetricsSummary` from any iterable of `Event`
objects (file stream, in-memory list, or `TraceStore` result).

```python
import agentobs
from agentobs.stream import EventStream

events  = list(EventStream.from_file("events.jsonl"))
summary = agentobs.metrics.aggregate(events)
```

---

## `MetricsSummary`

```python
@dataclass
class MetricsSummary:
    trace_count: int
    span_count: int
    agent_success_rate: float
    avg_trace_duration_ms: float
    p50_trace_duration_ms: float
    p95_trace_duration_ms: float
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    llm_latency_ms: LatencyStats
    tool_failure_rate: float
    token_usage_by_model: dict[str, TokenUsage]
    cost_by_model: dict[str, float]
```

| Field | Description |
|---|---|
| `trace_count` | Number of distinct trace IDs in the input |
| `span_count` | Total number of spans |
| `agent_success_rate` | Fraction of traces that contain no error spans (0–1) |
| `avg_trace_duration_ms` | Mean end-to-end duration across all traces |
| `p50_trace_duration_ms` | Median trace duration |
| `p95_trace_duration_ms` | 95th-percentile trace duration |
| `total_input_tokens` | Sum of all `input_tokens` values across LLM spans |
| `total_output_tokens` | Sum of all `output_tokens` values |
| `total_cost_usd` | Sum of all `cost_usd` values |
| `llm_latency_ms` | `LatencyStats(min, max, p50, p95, p99)` for LLM spans |
| `tool_failure_rate` | Fraction of tool spans with `status="error"` (0–1) |
| `token_usage_by_model` | Per-model `TokenUsage` aggregate |
| `cost_by_model` | Per-model USD total |

---

## `LatencyStats`

```python
@dataclass
class LatencyStats:
    min: float
    max: float
    p50: float
    p95: float
    p99: float
```

---

## Single-metric helpers

All helpers accept the same `Iterable[Event]` as `aggregate()` and return
a scalar or simple dataclass computed from a single pass.

### `agent_success_rate(events) -> float`

Fraction of traces (by `trace_id`) that contain no event with
`status="error"` or `status="timeout"`.

### `llm_latency(events) -> LatencyStats`

Latency percentiles computed from all `llm_call` span durations.

### `tool_failure_rate(events) -> float`

Fraction of `tool_call` spans with `status="error"`.

### `token_usage(events) -> dict[str, TokenUsage]`

Per-model `TokenUsage` aggregate keyed by `ModelInfo.name`.

---

## Usage with `TraceStore`

```python
import agentobs
from agentobs._store import get_store

agentobs.configure(exporter="console", enable_trace_store=True)

# ... run your agent ...

events  = agentobs.get_last_agent_run() or []
summary = agentobs.metrics.aggregate(events)
```
