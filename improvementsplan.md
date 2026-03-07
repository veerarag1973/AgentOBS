# AgentOBS SDK — Phase-wise Implementation Plan

**Scope**: All features from `roadmap.md` that are either partially implemented or not yet implemented.
**Total features**: 13 (9 partial + 4 missing)
**Reference date**: March 7, 2026

---

## Summary of Work

| Feature | Roadmap # | Status | Phase |
|---|---|---|---|
| Core Trace Object (`Trace` class + `start_trace()`) | #1 | Partial | 1 |
| Context Propagation (`contextvars`, async) | #3 | Partial | 1 |
| Async Context Manager API (`async with tracer.span()`) | #4 / #11 | Partial | 1 |
| Event Logging Inside Spans (`span.add_event()`) | #18 | Missing | 2 |
| Error Telemetry (distinct error types, auto-timeout) | #8 | Partial | 2 |
| LLM Span Schema (`temperature`, optional raw fields) | #6 | Partial | 2 |
| Tool Span Schema (optional raw `arguments`/`result`) | #7 | Partial | 2 |
| Debug Utilities (`print_tree`, `summary`, `visualize`) | #13 | Partial | 3 |
| Sampling Controls (`sample_rate`, `error_sampling`) | #10 | Missing | 3 |
| Metrics Extraction API (`agentobs.metrics.aggregate`) | #19 | Missing | 4 |
| MCP Trace Access APIs (`get_trace`, `list_tool_calls`) | #16 | Missing | 4 |
| Framework Integration Hooks (CrewAI + hook API) | #15 | Partial | 5 |

---

## Phase 1 — Core Foundation

**Goal**: Replace threading.local with `contextvars`, add a `Trace` class and `start_trace()`, and enable `async with tracer.span()`. Everything in later phases depends on correct async context propagation.

**Files to modify/create**:
- `agentobs/_span.py`
- `agentobs/_tracer.py`
- `agentobs/__init__.py`

---

### 1.1 — Replace `threading.local` with `contextvars` (#3)

**Current state**: `_span.py` uses `threading.local()` for `_span_stack`, `_run_stack`, and `_step_list`. Context does not flow across `asyncio` tasks or `concurrent.futures` thread pools.

**Work required**:

1. Replace the three `threading.local` stacks with `contextvars.ContextVar`:
   ```python
   # Before (in _span.py)
   _tl = threading.local()
   
   def _get_span_stack() -> list[Span]:
       if not hasattr(_tl, "span_stack"):
           _tl.span_stack = []
       return _tl.span_stack
   
   # After
   import contextvars
   _span_stack_var: contextvars.ContextVar[list[Span]] = contextvars.ContextVar(
       "agentobs_span_stack", default=None
   )
   
   def _get_span_stack() -> list[Span]:
       stack = _span_stack_var.get()
       if stack is None:
           stack = []
           _span_stack_var.set(stack)
       return stack
   ```

2. Apply the same pattern to `_run_stack` and `_step_list`.

3. In `SpanContextManager.__enter__`, capture the current token so we can restore the stack slice on exit:
   ```python
   self._token = _span_stack_var.set(list(current_stack) + [span])
   ```
   On `__exit__`: `_span_stack_var.reset(self._token)`.

4. Provide a `copy_context()` helper for passing context into manually spawned threads or `loop.run_in_executor` callsites:
   ```python
   # agentobs/_span.py
   def copy_context() -> contextvars.Context:
       """Return a copy of the current context for spawning tasks/threads."""
       return contextvars.copy_context()
   ```

**Backward compatibility**: sync code is unaffected — `contextvars` works transparently for threads too.

---

### 1.2 — Async Context Managers on Tracer (#4 / #11)

**Current state**: `SpanContextManager`, `AgentRunContextManager`, and `AgentStepContextManager` only implement `__enter__` / `__exit__`. Async code cannot use `async with tracer.span(...)`.

**Work required**:

1. Add `__aenter__` and `__aexit__` to `SpanContextManager`:
   ```python
   async def __aenter__(self) -> Span:
       return self.__enter__()          # identical logic; stack is now ContextVar-safe

   async def __aexit__(
       self,
       exc_type: type[BaseException] | None,
       exc_val: BaseException | None,
       exc_tb: TracebackType | None,
   ) -> bool:
       return self.__exit__(exc_type, exc_val, exc_tb)
   ```

2. Apply the same to `AgentRunContextManager` and `AgentStepContextManager`.

3. No new public API needed — `tracer.span()` already returns a `SpanContextManager`; adding `__aenter__`/`__aexit__` to it makes `async with tracer.span(...)` work without any API change.

4. Add `asyncio` streaming support hook — when a span wraps an async generator (streaming LLM), ensure the span stays open until the generator is exhausted:
   ```python
   # agentobs/_span.py
   async def wrap_async_gen(self, agen):
       """Keep this span open for the full lifetime of an async generator."""
       async with self:
           async for item in agen:
               yield item
   ```

**Testing**: Add async tests using `asyncio.gather` to verify two concurrent tasks each see their own parent span.

---

### 1.3 — `Trace` Class and `start_trace()` (#1)

**Current state**: No `Trace` object. Trace identity is an implicit `trace_id` string. Developers cannot hold a reference to "the current trace" or build a trace imperatively.

**Work required**:

1. Create a `Trace` class in `agentobs/_span.py` (or a new `agentobs/_trace.py`):
   ```python
   @dataclass
   class Trace:
       trace_id: str
       agent_name: str
       service_name: str
       start_time: float          # Unix seconds
       spans: list[Span] = field(default_factory=list)
       _end_time: float | None = None

       def llm_call(self, model: str, **kwargs) -> SpanContextManager:
           """Convenience: open a child span of type llm_call."""
           ...

       def tool_call(self, tool_name: str, **kwargs) -> SpanContextManager:
           """Convenience: open a child span of type tool_call."""
           ...

       def end(self) -> None:
           """Mark the trace as complete; flushes pending spans."""
           self._end_time = time.time()

       def to_json(self) -> str:
           """Serialize the full trace to JSON."""
           ...

       def save(self, path: str) -> None:
           """Write the trace as NDJSON to the given path."""
           ...

       def print_tree(self) -> None:    # See Phase 3
           ...

       def summary(self) -> dict:       # See Phase 3
           ...
   ```

2. Add `start_trace()` to `agentobs/_tracer.py` and re-export from `agentobs/__init__.py`:
   ```python
   def start_trace(agent_name: str, **attributes) -> Trace:
       """Begin a new trace; returns a Trace object that acts as a root context."""
       ...
   ```

3. The `Trace` object holds a reference to its root `SpanContextManager` and pushes onto the `ContextVar` stack so all child spans automatically inherit the `trace_id`.

4. Export `Trace`, `start_trace` in `agentobs/__init__.py`.

---

## Phase 2 — Observability Completeness

**Goal**: Fill field-level gaps in span schemas, add structured event logging inside spans, and introduce clearly-typed error categories.

**Files to modify/create**:
- `agentobs/namespaces/trace.py`
- `agentobs/_span.py`
- `agentobs/exceptions.py`
- `agentobs/_stream.py`

---

### 2.1 — Event Logging Inside Spans (`span.add_event()`) (#18)

**Current state**: Spans have fixed fields (`tool_calls`, `reasoning_steps`) but no open-ended event log. `Span.attributes` is flat key-value only.

**Work required**:

1. Add a `SpanEvent` dataclass to `agentobs/namespaces/trace.py`:
   ```python
   @dataclass
   class SpanEvent:
       name: str
       timestamp_ns: int = field(default_factory=time.time_ns)
       metadata: dict[str, Any] = field(default_factory=dict)

       def to_dict(self) -> dict:
           return {
               "name": self.name,
               "timestamp_ns": self.timestamp_ns,
               "metadata": self.metadata,
           }
   ```

2. Add `events: list[SpanEvent]` to `SpanPayload` (optional, default empty list).

3. Add `add_event()` method to `Span`:
   ```python
   def add_event(self, name: str, metadata: dict[str, Any] | None = None) -> None:
       """Record a named event at this point in time within the span."""
       self.events.append(SpanEvent(name=name, metadata=metadata or {}))
   ```

4. Ensure `SpanPayload.to_dict()` / `from_dict()` include `events`.

---

### 2.2 — Error Telemetry Improvements (#8)

**Current state**: All errors emit `TRACE_SPAN_FAILED`. Error category (llm, tool, timeout) is a free-form string only.

**Work required**:

1. Add typed error categories as a string literal type in `agentobs/types.py`:
   ```python
   SpanErrorCategory = Literal[
       "agent_error", "llm_error", "tool_error", "timeout_error", "unknown_error"
   ]
   ```

2. Enhance `Span.record_error()` to accept an optional `category`:
   ```python
   def record_error(
       self,
       exc: BaseException,
       category: SpanErrorCategory = "unknown_error",
   ) -> None:
       self.status = "error"
       self.error = str(exc)
       self.error_type = type(exc).__qualname__
       self.error_category = category
   ```

3. Map common built-in exception types to categories automatically:
   - `TimeoutError`, `asyncio.TimeoutError` → `"timeout_error"`
   - Exceptions originating inside LLM integration patches → `"llm_error"`
   - Exceptions originating inside tool call spans → `"tool_error"`

4. Add `error_category` to `SpanPayload` and include it in `to_dict()`.

5. Auto-timeout detection: add `Span.set_timeout_deadline(seconds: float)` that schedules a background `threading.Timer` / `asyncio` task to set `status = "timeout"` if the span is not closed within the deadline.

---

### 2.3 — LLM Span Schema Additions (#6)

**Current state**: `SpanPayload` has model, provider, tokens, cost, latency. Missing: `temperature` as a first-class field. Raw prompt/response intentionally excluded by RFC, so those remain off.

**Work required**:

1. Add `temperature: float | None = None` to `SpanPayload` in `agentobs/namespaces/trace.py`.

2. Add `top_p: float | None = None`, `max_tokens: int | None = None` as optional fields.

3. Update `SpanPayload.to_dict()` / `from_dict()` to include these fields.

4. Update the `tracer.span()` signature to accept `temperature` and `top_p`:
   ```python
   def span(
       self,
       name: str,
       *,
       model: str | None = None,
       operation: str = "chat",
       temperature: float | None = None,
       top_p: float | None = None,
       max_tokens: int | None = None,
       attributes: dict[str, Any] | None = None,
   ) -> SpanContextManager:
   ```

5. The LLM integration patches (OpenAI, Anthropic, Groq, etc.) should extract `temperature` from the call kwargs and store it on the span.

---

### 2.4 — Tool Span Schema Additions (#7)

**Current state**: `ToolCall.arguments_hash` (SHA-256). Raw arguments intentionally hashed per RFC §20.4.

**Work required**:

1. Add an opt-in `include_raw_tool_io: bool = False` flag to `AgentOBSConfig`. When `True` (and no `RedactionPolicy` blocks it), raw arguments and result are stored.

2. Add optional `arguments_raw: str | None = None` and `result_raw: str | None = None` fields to `ToolCall`. These are populated only when `include_raw_tool_io=True`.

3. When a `RedactionPolicy` is configured, pass raw values through `redact.redact_value()` before storage.

4. Add `retry_count: int | None = None` and `external_api: str | None = None` to `ToolCall`.

5. Update `ToolCall.to_dict()` / `from_dict()`.

---

## Phase 3 — Developer Experience

**Goal**: Make the SDK enjoyable to use during development. Add visual debugging and production-safe trace sampling.

**Files to modify/create**:
- `agentobs/_span.py` (or `agentobs/_trace.py`)
- `agentobs/_tracer.py`
- `agentobs/config.py`
- `agentobs/_stream.py`
- New: `agentobs/debug.py`

---

### 3.1 — Debug Utilities (`print_tree`, `summary`, `visualize`) (#13)

**Current state**: `SyncConsoleExporter` prints individual span boxes. No tree view, no summary, no visualization.

**Work required**:

1. Create `agentobs/debug.py` with standalone functions (also available as methods on `Trace`):

   **`print_tree(spans: list[SpanPayload] | list[Span])`**
   ```
   Agent Run: research-agent  [2.4s]
    ├─ LLM Call: gpt-4o  [1.1s]  in=512 out=200 tokens  $0.0031
    ├─ Tool Call: search  [0.4s]  ok
    │   └─ Tool Call: fetch_url  [0.2s]  ok
    └─ LLM Call: gpt-4o  [0.9s]  in=300 out=150 tokens  $0.0021
   ```
   Implementation: group spans by `trace_id`, sort by `start_time`, build parent→children map from `parent_span_id`, then DFS-print with Unicode box-drawing characters. Respect `NO_COLOR` env var.

   **`summary(spans: list[SpanPayload]) -> dict`**
   ```python
   {
       "trace_id": "...",
       "agent_name": "research-agent",
       "total_duration_ms": 2400.0,
       "span_count": 4,
       "llm_calls": 2,
       "tool_calls": 1,
       "total_input_tokens": 812,
       "total_output_tokens": 350,
       "total_cost_usd": 0.0052,
       "errors": 0,
   }
   ```

   **`visualize(spans: list[SpanPayload], output: str = "html") -> str`**
   Generates a self-contained HTML string with an inline timeline (using HTML/CSS only, no external dependencies) showing spans as Gantt-style bars. Output can be written to a file with `visualize(..., path="trace.html")`.

2. Export `print_tree`, `summary`, `visualize` from `agentobs/__init__.py`.

3. Wire `Trace.print_tree()` and `Trace.summary()` to call these functions with the trace's accumulated spans.

---

### 3.2 — Sampling Controls (#10)

**Current state**: Every span is always emitted. No way to reduce telemetry volume in production.

**Work required**:

1. Add sampling fields to `AgentOBSConfig`:
   ```python
   sample_rate: float = 1.0        # 0.0–1.0; fraction of traces to emit
   always_sample_errors: bool = True  # always emit spans with status="error"
   trace_filters: list[Callable[[Event], bool]] = field(default_factory=list)
   ```

2. Implement sampling in `agentobs/_stream._dispatch()`:
   ```python
   def _should_emit(event: Event, cfg: AgentOBSConfig) -> bool:
       # Always emit errors if always_sample_errors
       if cfg.always_sample_errors and _is_error_event(event):
           return True
       # Probabilistic sampling — decision is per trace_id for consistency
       if cfg.sample_rate < 1.0:
           trace_id = event.payload.get("trace_id", "")
           # Deterministic: hash trace_id so all spans of a trace are sampled together
           h = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16)
           if (h / 0xFFFFFFFF) > cfg.sample_rate:
               return False
       # Custom filters
       return all(f(event) for f in cfg.trace_filters)
   ```

3. Update `configure()` to accept `sample_rate`, `always_sample_errors`, `trace_filters`.

4. Add `AGENTOBS_SAMPLE_RATE` env var override.

---

## Phase 4 — Production Analytics

**Goal**: Enable programmatic extraction of metrics from traces and provide an in-process queryable trace store.

**Files to create**:
- `agentobs/metrics.py`
- `agentobs/_store.py`

---

### 4.1 — Metrics Extraction API (#19)

**Current state**: Per-span data is rich (`token_usage`, `cost`, `duration_ms`) but there is no aggregation API. Users must read JSONL and compute manually.

**Work required**:

1. Create `agentobs/metrics.py`:

   ```python
   from agentobs.metrics import aggregate, agent_success_rate, llm_latency, tool_failure_rate

   # Single-call aggregation
   result = agentobs.metrics.aggregate(traces)
   # result is a MetricsSummary dataclass
   ```

2. `MetricsSummary` dataclass:
   ```python
   @dataclass
   class MetricsSummary:
       trace_count: int
       span_count: int
       agent_success_rate: float          # fraction of traces with no error spans
       avg_trace_duration_ms: float
       p50_trace_duration_ms: float
       p95_trace_duration_ms: float
       total_input_tokens: int
       total_output_tokens: int
       total_cost_usd: float
       llm_latency_ms: LatencyStats        # min, max, p50, p95, p99
       tool_failure_rate: float            # fraction of tool_call spans with status="error"
       token_usage_by_model: dict[str, TokenUsage]
       cost_by_model: dict[str, float]
   ```

3. Core functions:
   - `aggregate(events: Iterable[Event]) -> MetricsSummary`
   - `agent_success_rate(events: Iterable[Event]) -> float`
   - `llm_latency(events: Iterable[Event]) -> LatencyStats`
   - `tool_failure_rate(events: Iterable[Event]) -> float`
   - `token_usage(events: Iterable[Event]) -> dict[str, TokenUsage]`

4. All functions accept `Iterable[Event]` so they work with:
   - `EventStream.from_file("events.jsonl")`
   - An in-memory list of events
   - The `TraceStore` (Phase 4.2)

5. Export from `agentobs/__init__.py`:
   ```python
   from agentobs import metrics
   summary = metrics.aggregate(events)
   ```

---

### 4.2 — MCP Trace Access APIs (#16)

**Current state**: Events are fire-and-forget. There is no in-process queryable store.

**Work required**:

1. Create `agentobs/_store.py` — an in-memory ring buffer that retains the last N traces:
   ```python
   class TraceStore:
       def __init__(self, max_traces: int = 100): ...

       def record(self, event: Event) -> None:
           """Called by _stream._dispatch() when store is enabled."""

       def get_trace(self, trace_id: str) -> list[Event] | None:
           """All events belonging to this trace_id."""

       def get_last_agent_run(self) -> list[Event] | None:
           """Events for the most recently completed agent_run trace."""

       def list_tool_calls(self, trace_id: str) -> list[SpanPayload]:
           """All tool_call spans within a trace."""

       def list_llm_calls(self, trace_id: str) -> list[SpanPayload]:
           """All llm-type spans within a trace."""

       def clear(self) -> None: ...
   ```

2. Add `enable_trace_store: bool = False` and `trace_store_size: int = 100` to `AgentOBSConfig`.

3. Wire `_stream._dispatch()` to also call `_store.record(event)` when the store is enabled.

4. Expose module-level access functions in `agentobs/__init__.py`:
   ```python
   def get_trace(trace_id: str) -> list[Event] | None: ...
   def get_last_agent_run() -> list[Event] | None: ...
   def list_tool_calls(trace_id: str) -> list[SpanPayload]: ...
   def list_llm_calls(trace_id: str) -> list[SpanPayload]: ...
   ```

5. Add `AGENTOBS_ENABLE_TRACE_STORE=1` env var.

6. **Security note**: The store holds raw event payloads in memory. When a `RedactionPolicy` is configured, events must be redacted before storage. Document the memory overhead (ring buffer bounded to `trace_store_size`).

---

## Phase 5 — Ecosystem Expansion

**Goal**: Extend framework coverage to CrewAI and add a stable hook registration API for custom integrations.

**Files to create/modify**:
- New: `agentobs/integrations/crewai.py`
- `agentobs/integrations/__init__.py`
- New: `agentobs/_hooks.py`
- `agentobs/__init__.py`

---

### 5.1 — Standalone Hook Registration API (#15 — hook API)

**Current state**: `on_llm_start` / `on_llm_end` etc. are embedded inside the LangChain/LlamaIndex handlers only. No way for developers to register custom hooks globally.

**Work required**:

1. Create `agentobs/_hooks.py`:
   ```python
   HookFn = Callable[[Span], None]

   class HookRegistry:
       def on_agent_start(self, fn: HookFn) -> HookFn: ...   # decorator
       def on_agent_end(self, fn: HookFn) -> HookFn: ...
       def on_llm_call(self, fn: HookFn) -> HookFn: ...
       def on_tool_call(self, fn: HookFn) -> HookFn: ...
       def clear(self) -> None: ...

   hooks = HookRegistry()   # module-level singleton
   ```

2. Fire hooks from `SpanContextManager.__enter__` (start hooks) and `__exit__` (end hooks).

3. Example usage:
   ```python
   @agentobs.hooks.on_llm_call
   def my_hook(span: Span) -> None:
       print(f"LLM called: {span.model}")
   ```

4. Export `hooks` from `agentobs/__init__.py`.

---

### 5.2 — CrewAI Integration (#15 — CrewAI)

**Current state**: Not implemented. CrewAI uses a callback / event system similar to LangChain.

**Work required**:

1. Create `agentobs/integrations/crewai.py`:
   ```python
   class AgentOBSCrewAIHandler:
       """CrewAI event handler that emits AgentOBS trace events."""

       def on_agent_action(self, agent, task, tool, tool_input): ...
       def on_agent_finish(self, agent, output): ...
       def on_tool_start(self, tool, input): ...
       def on_tool_end(self, tool, output): ...
       def on_task_start(self, task): ...
       def on_task_end(self, task, output): ...
   ```

2. Handler follows the same pattern as `LLMSchemaCallbackHandler` — it manages a `SpanContextManager`, pushes agent steps as child spans, and records token usage if available from CrewAI's output.

3. Provide a `patch()` convenience function that registers the handler into CrewAI automatically (guards with `importlib.util.find_spec("crewai")` so the module imports cleanly when CrewAI is not installed).

4. Document in `docs/integrations/crewai.md`.

---

## Testing Requirements Per Phase

| Phase | Required tests |
|---|---|
| 1 | `asyncio.gather` concurrent spans; context isolation across tasks; `async with tracer.span()` timing; `Trace` serialization roundtrip |
| 2 | `span.add_event()` survives roundtrip; `temperature` field in emitted payload; `record_error(category="llm_error")` sets field; sampling determinism across spans of same trace |
| 3 | `print_tree()` output matches expected ASCII; `summary()` token totals; HTML output from `visualize()`; sampling rate within 5% of configured value over 10 000 spans |
| 4 | `metrics.aggregate()` correct rates; `TraceStore.get_trace()` returns only spans of requested trace; ring buffer eviction at `max_traces` |
| 5 | Hook fires on correct span types; CrewAI mock integration emits correct event types |

---

## Dependency Graph

```
Phase 1 (contextvars + async + Trace)
    │
    ├──► Phase 2 (add_event, error types, schema fields)
    │        │
    │        └──► Phase 3 (debug utils + sampling)
    │                    │
    │                    └──► Phase 4 (metrics + MCP store)
    │                               │
    └──────────────────────────────►└──► Phase 5 (hooks + CrewAI)
```

Phase 1 is a prerequisite for everything. Phases 2–5 can be parallelised across developers once Phase 1 lands.

---

## Estimated Scope per Phase

| Phase | New files | Modified files | Approx. new lines |
|---|---|---|---|
| 1 | `_trace.py` | `_span.py`, `_tracer.py`, `__init__.py` | ~300 |
| 2 | — | `trace.py`, `_span.py`, `config.py`, `_stream.py`, `exceptions.py` | ~250 |
| 3 | `debug.py` | `config.py`, `_stream.py`, `__init__.py` | ~250 |
| 4 | `metrics.py`, `_store.py` | `config.py`, `_stream.py`, `__init__.py` | ~350 |
| 5 | `integrations/crewai.py`, `_hooks.py` | `__init__.py`, `integrations/__init__.py` | ~250 |
