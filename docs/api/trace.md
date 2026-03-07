# agentobs._trace

High-level tracing entry point: the `Trace` class and the `start_trace()`
factory function.

---

## `start_trace`

```python
def start_trace(agent_name: str, **attributes: Any) -> Trace
```

Open a new trace for a single agent run. Pushes a root
`AgentRunContextManager` onto the `contextvars` span stack so all child
spans created within the context inherit the `trace_id` automatically.

**Parameters**

| Parameter | Type | Description |
|---|---|---|
| `agent_name` | `str` | Non-empty name for the agent (e.g. `"research-agent"`) |
| `**attributes` | `Any` | Extra key-value pairs stored in the root span's `attributes` dict |

**Returns** — a `Trace` object.

**Context manager** (both sync and async):

```python
# Sync
with agentobs.start_trace("my-agent") as trace:
    ...

# Async
async with agentobs.start_trace("my-agent") as trace:
    ...

# Imperative
trace = agentobs.start_trace("my-agent")
try:
    ...
finally:
    trace.end()
```

---

## `Trace`

```python
@dataclass
class Trace:
    trace_id: str
    agent_name: str
    service_name: str
    start_time: float           # Unix epoch seconds
    spans: list[Span]
```

Created by `start_trace()`. Do not construct directly.

### Methods

#### `llm_call(model, *, operation="chat", temperature=None, top_p=None, max_tokens=None, attributes=None) -> SpanContextManager`

Open a child span of type `llm_call`. All keyword arguments map directly to
`SpanPayload` fields.

#### `tool_call(tool_name, *, attributes=None) -> SpanContextManager`

Open a child span of type `tool_call`.

#### `end() -> None`

Mark the trace as complete and flush any pending spans. Called automatically
when exiting the context manager.

#### `to_json(*, indent=None) -> str`

Serialise the full trace (all accumulated spans) to JSON.

#### `save(path: str) -> None`

Write the trace as NDJSON (one JSON object per line) to `path`.

#### `print_tree(*, file=None) -> None`

Pretty-print the span tree. Delegates to `agentobs.debug.print_tree()`.

#### `summary() -> dict`

Return aggregated statistics. Delegates to `agentobs.debug.summary()`.

---

## Re-exports

`Trace` and `start_trace` are re-exported at the top-level `agentobs` package:

```python
from agentobs import Trace, start_trace
```
