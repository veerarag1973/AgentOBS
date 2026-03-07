# Metrics & Analytics

AgentOBS 2.0 ships two complementary APIs for extracting insights from
recorded traces:

- **`agentobs.metrics`** — batch aggregation over any `Iterable[Event]`
- **`agentobs._store.TraceStore`** — in-process ring buffer for real-time
  programmatic access

---

## `agentobs.metrics`

### `aggregate(events) -> MetricsSummary`

Compute a comprehensive summary from any iterable of `Event` objects:

```python
import agentobs
from agentobs.stream import EventStream

# From a JSONL file
events  = list(EventStream.from_file("events.jsonl"))
summary = agentobs.metrics.aggregate(events)

print(f"Traces  : {summary.trace_count}")
print(f"Success : {summary.agent_success_rate:.0%}")
print(f"p50 LLM : {summary.llm_latency_ms.p50:.0f} ms")
print(f"p95 LLM : {summary.llm_latency_ms.p95:.0f} ms")
print(f"Tokens  : {summary.total_input_tokens} in / {summary.total_output_tokens} out")
print(f"Cost    : ${summary.total_cost_usd:.4f}")
print(f"By model: {summary.cost_by_model}")
```

### `MetricsSummary` fields

| Field | Type | Description |
|---|---|---|
| `trace_count` | `int` | Number of distinct traces |
| `span_count` | `int` | Total span count across all traces |
| `agent_success_rate` | `float` | Fraction of traces with no error spans (0–1) |
| `avg_trace_duration_ms` | `float` | Mean end-to-end trace duration in ms |
| `p50_trace_duration_ms` | `float` | Median trace duration |
| `p95_trace_duration_ms` | `float` | 95th-percentile trace duration |
| `total_input_tokens` | `int` | Sum of `input_tokens` across all LLM spans |
| `total_output_tokens` | `int` | Sum of `output_tokens` across all LLM spans |
| `total_cost_usd` | `float` | Sum of `cost_usd` across all spans |
| `llm_latency_ms` | `LatencyStats` | `min`, `max`, `p50`, `p95`, `p99` for LLM spans |
| `tool_failure_rate` | `float` | Fraction of tool spans with `status="error"` |
| `token_usage_by_model` | `dict[str, TokenUsage]` | Per-model input/output/total token sums |
| `cost_by_model` | `dict[str, float]` | Per-model USD totals |

### Single-metric helpers

```python
from agentobs import metrics

success   = metrics.agent_success_rate(events)    # float
latency   = metrics.llm_latency(events)           # LatencyStats
fail_rate = metrics.tool_failure_rate(events)      # float
by_model  = metrics.token_usage(events)           # dict[str, TokenUsage]
```

All helpers accept the same `Iterable[Event]` as `aggregate()`.

---

## `TraceStore` — in-process ring buffer

Enable the trace store to query recent traces programmatically without
reading any files:

```python
agentobs.configure(
    exporter="console",
    enable_trace_store=True,
    trace_store_size=200,   # keep the last 200 traces (default: 100)
)
```

### Querying the store

```python
# Get all events for a specific trace
events = agentobs.get_trace(trace_id)

# Get the most recently completed agent run
events = agentobs.get_last_agent_run()

# Get all tool-call spans in a trace
tool_spans = agentobs.list_tool_calls(trace_id)

# Get all LLM spans in a trace
llm_spans  = agentobs.list_llm_calls(trace_id)
```

Or use the `TraceStore` object directly:

```python
from agentobs._store import get_store

store = get_store()
events = store.get_trace(trace_id)
store.clear()
```

### Environment variable

```bash
export AGENTOBS_ENABLE_TRACE_STORE=1
```

### Security note

The store holds event payloads in memory. When a `RedactionPolicy` is
configured (`agentobs.configure(redaction_policy=...)`), events are
redacted **before** being passed to `store.record()`. Raw PII never enters
the ring buffer.

Memory overhead is bounded: `trace_store_size × average_events_per_trace`.
At the default of 100 traces and ~20 events per trace, the store holds at
most ~2 000 `Event` objects.
