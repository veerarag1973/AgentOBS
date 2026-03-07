# Debugging & Visualization

AgentOBS 2.0 ships a `debug` module with three tools for inspecting traces
during development, plus production-safe sampling controls.

---

## `print_tree(spans, *, file=None)`

Pretty-print a hierarchical span tree to stdout.

```python
import agentobs

agentobs.configure(exporter="console", service_name="my-agent")

with agentobs.start_trace("research-agent") as trace:
    with trace.llm_call("gpt-4o") as span:
        span.set_token_usage(input=512, output=200, total=712)
        span.set_status("ok")
    with trace.tool_call("web_search") as span:
        span.set_status("ok")

trace.print_tree()
```

Example output:

```
— Agent Run: research-agent  [1.2s]
 ├─ LLM Call: gpt-4o  [0.8s]  in=512 out=200 tokens  $0.0034
 └─ Tool Call: web_search  [0.4s]  ok
```

`print_tree()` can also be called as a standalone function:

```python
from agentobs.debug import print_tree
from agentobs.stream import EventStream

events = list(EventStream.from_file("events.jsonl"))
spans  = [e for e in events if "span_name" in e.payload]
print_tree(spans)
```

> **Tip:** Set `NO_COLOR=1` in your environment to suppress ANSI colour codes
> (e.g. in CI pipelines or when piping to a file).

---

## `summary(spans) -> dict`

Returns an aggregated statistics dictionary for a collection of spans.

```python
from agentobs.debug import summary

stats = trace.summary()
# or:
stats = summary(spans)

print(stats)
# {
#   'trace_id': '01JP...',
#   'agent_name': 'research-agent',
#   'total_duration_ms': 1200.0,
#   'span_count': 3,
#   'llm_calls': 1,
#   'tool_calls': 1,
#   'total_input_tokens': 512,
#   'total_output_tokens': 200,
#   'total_cost_usd': 0.0034,
#   'errors': 0,
# }
```

---

## `visualize(spans, output="html", *, path=None) -> str`

Generate a self-contained Gantt-timeline HTML page — no external
dependencies, no network calls.

```python
from agentobs.debug import visualize

# Return as a string
html = visualize(trace.spans)

# Write directly to a file
visualize(trace.spans, path="trace.html")
```

The generated page shows each span as a proportional horizontal bar on a
shared timeline axis.  Hovering over a bar shows the span name, duration,
model/tool name, token counts, and cost.

---

## Sampling controls

In production you often want to emit only a fraction of traces to reduce
telemetry volume. Sampling is configured via `AgentOBSConfig`:

```python
agentobs.configure(
    exporter="otlp",
    otlp_endpoint="http://otel-collector:4318",
    sample_rate=0.1,           # emit 10 % of traces
    always_sample_errors=True, # always emit error/timeout spans
)
```

Or via environment variable:

```bash
export AGENTOBS_SAMPLE_RATE=0.25
```

### How sampling works

- The sampling decision is made **per `trace_id`** (deterministic SHA-256
  hash), so all spans of a trace are either all emitted or all dropped —
  you never see a partial trace.
- `always_sample_errors=True` (the default) ensures that any span with
  `status="error"` or `status="timeout"` is always emitted regardless of
  `sample_rate`.
- Set `sample_rate=1.0` (the default) to disable sampling.
- Set `sample_rate=0.0` to drop all traces except errors.

### Custom trace filters

Add arbitrary per-event predicates that run after the probabilistic gate:

```python
from agentobs import configure, Event

def only_expensive_traces(event: Event) -> bool:
    cost = event.payload.get("cost_usd", 0)
    return cost > 0.01  # only emit spans that cost more than $0.01

configure(
    exporter="console",
    sample_rate=1.0,
    trace_filters=[only_expensive_traces],
)
```
