# agentobs._store

In-memory ring buffer that retains the last N traces for programmatic access.

---

## Enable the store

```python
import agentobs

agentobs.configure(
    exporter="console",
    enable_trace_store=True,
    trace_store_size=200,   # default: 100
)
```

Or via environment variable:

```bash
export AGENTOBS_ENABLE_TRACE_STORE=1
```

---

## Module-level convenience functions

These are re-exported at the top-level `agentobs` package:

```python
agentobs.get_trace(trace_id)       # -> list[Event] | None
agentobs.get_last_agent_run()      # -> list[Event] | None
agentobs.list_tool_calls(trace_id) # -> list[SpanPayload]
agentobs.list_llm_calls(trace_id)  # -> list[SpanPayload]
```

---

## `TraceStore`

```python
class TraceStore:
    def __init__(self, max_traces: int = 100) -> None
```

Thread-safe in-memory ring buffer.  When `max_traces` is reached the oldest
trace is evicted.

### `record(event: Event) -> None`

Called automatically by `_stream._dispatch()` when the store is enabled.
You do not normally need to call this directly.

### `get_trace(trace_id: str) -> list[Event] | None`

Return all events that share `trace_id`, in emission order.
Returns `None` if the trace is not in the buffer.

### `get_last_agent_run() -> list[Event] | None`

Return events for the most recently completed `agent_run` trace.

### `list_tool_calls(trace_id: str) -> list[SpanPayload]`

Return `SpanPayload` objects for every `tool_call` span within `trace_id`.

### `list_llm_calls(trace_id: str) -> list[SpanPayload]`

Return `SpanPayload` objects for every `llm_call` span within `trace_id`.

### `clear() -> None`

Evict all traces from the buffer.

---

## `get_store() -> TraceStore`

Return the global singleton `TraceStore` instance.

```python
from agentobs._store import get_store

store = get_store()
print(store)  # TraceStore(traces=42, max=100)
```

---

## Security

Events are redacted before reaching `record()` when a `RedactionPolicy`
is configured on the global config. Raw PII never enters the ring buffer.

Memory overhead is bounded to `trace_store_size × average_events_per_trace`.
At the default of 100 traces, the store retains at most a few thousand
`Event` objects.
