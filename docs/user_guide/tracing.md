# Tracing API

AgentOBS 2.0 ships a first-class `Trace` object and the `start_trace()` entry
point that replaces the low-level implicit `trace_id` string. Everything in
this guide builds on the async-safe `contextvars` context propagation also
introduced in 2.0.

---

## Quick start

```python
import agentobs

agentobs.configure(exporter="console", service_name="my-agent")

with agentobs.start_trace("research-agent") as trace:
    with trace.llm_call("gpt-4o", temperature=0.7) as span:
        result = call_llm(prompt)
        span.set_token_usage(input=512, output=200, total=712)
        span.set_status("ok")

    with trace.tool_call("web_search") as span:
        output = run_search("latest AI news")
        span.set_status("ok")

trace.print_tree()
```

---

## `start_trace(agent_name, **attributes) -> Trace`

Opens a new trace for one agent run. Under the hood it pushes a root
`AgentRunContextManager` onto the `contextvars` stack so all child spans
automatically inherit the `trace_id`.

```python
trace = agentobs.start_trace(
    "my-agent",
    service_name="backend",   # overrides configure() for this trace
    env="production",
)
```

`start_trace()` supports both sync and async context-manager protocols:

```python
# Sync
with agentobs.start_trace("my-agent") as trace:
    ...

# Async
async with agentobs.start_trace("my-agent") as trace:
    ...

# Imperative (manual close)
trace = agentobs.start_trace("my-agent")
try:
    ...
finally:
    trace.end()
```

---

## `Trace` methods

### `trace.llm_call(model, *, temperature=None, top_p=None, max_tokens=None, **kwargs)`

Opens a child span of type `llm_call`. The returned object is a
`SpanContextManager` so it works as a context manager:

```python
with trace.llm_call("gpt-4o", temperature=0.7, max_tokens=1024) as span:
    response = call_llm(...)
    span.set_token_usage(input=200, output=80, total=280)
    span.set_status("ok")
```

The `temperature`, `top_p`, and `max_tokens` values are stored in
`SpanPayload` and appear in exported events.

### `trace.tool_call(tool_name, **kwargs)`

Opens a child span of type `tool_call`:

```python
with trace.tool_call("database_query") as span:
    rows = db.execute(query)
    span.set_status("ok")
```

### `trace.end()`

Marks the trace as complete and flushes any pending spans. Called
automatically when exiting the context manager.

### `trace.to_json(*, indent=None) -> str`

Serialises the full trace (all accumulated spans) to a JSON string.

### `trace.save(path: str)`

Writes the trace as NDJSON to the given file path.

### `trace.print_tree(*, file=None)`

Pretty-prints the span tree to stdout (or `file`). Delegates to
`agentobs.debug.print_tree()`.

### `trace.summary() -> dict`

Returns aggregated statistics. Delegates to `agentobs.debug.summary()`.

---

## `Span.add_event(name, metadata=None)`

Record a named, timestamped event at any point during a span's lifetime:

```python
with trace.llm_call("gpt-4o") as span:
    span.add_event("prompt_rendered", {"template": "v3", "tokens": 200})
    result = call_llm(...)
    span.add_event("response_received", {"finish_reason": "stop"})
    span.set_status("ok")
```

Events are included in the exported `SpanPayload.events` list and survive
`to_dict()` / `from_dict()` round-trips.

---

## Error handling and categories

### `Span.record_error(exc, category="unknown_error")`

```python
from agentobs.types import SpanErrorCategory  # type alias

try:
    result = call_llm(prompt)
except TimeoutError as exc:
    span.record_error(exc, category="timeout_error")
    # error_category is set automatically for TimeoutError too
except Exception as exc:
    span.record_error(exc, category="llm_error")
```

Built-in auto-mapping:

| Exception type | Auto-mapped category |
|---|---|
| `TimeoutError`, `asyncio.TimeoutError` | `"timeout_error"` |
| Exceptions in LLM integration spans | `"llm_error"` |
| Exceptions in tool call spans | `"tool_error"` |
| All others | `"unknown_error"` |

### `Span.set_timeout_deadline(seconds: float)`

Start a background timer that closes the span with `status="timeout"` if it
is not closed within `seconds`:

```python
with trace.llm_call("gpt-4o") as span:
    span.set_timeout_deadline(30.0)   # fail-safe: mark as timeout after 30 s
    result = await asyncio.wait_for(call_llm_async(prompt), timeout=30.0)
    span.set_status("ok")
```

---

## Async context managers

All context managers support `async with` natively:

```python
async with agentobs.start_trace("async-agent") as trace:
    async with trace.llm_call("gpt-4o") as span:
        response = await async_call_llm(prompt)
        span.set_status("ok")
```

Span stacks are stored in `contextvars.ContextVar` so two concurrent
`asyncio.gather()` tasks each maintain their own independent span hierarchy.

---

## Context propagation in threads

Use `agentobs.copy_context()` to carry the current span context into
manually spawned threads or `loop.run_in_executor` callsites:

```python
import concurrent.futures
import agentobs

ctx = agentobs.copy_context()

with concurrent.futures.ThreadPoolExecutor() as pool:
    future = pool.submit(ctx.run, my_function_that_creates_spans)
```

---

## Raw tool I/O (opt-in)

By default, tool arguments and results are stored only as a SHA-256 hash.
To store the raw strings (after redaction):

```python
agentobs.configure(
    exporter="console",
    include_raw_tool_io=True,
    redaction_policy=policy,   # recommended when raw I/O is enabled
)
```

When enabled, `ToolCall.arguments_raw` and `ToolCall.result_raw` are
populated. Any `RedactionPolicy` configured on the global config is applied
to these values before storage.
