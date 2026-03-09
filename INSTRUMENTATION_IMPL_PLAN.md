# AgentOBS SDK — Instrumentation Tools Implementation Plan

**Source:** SpanForge Master Ecosystem Build List (March 2026)  
**Scope:** Tools listed in the document that constitute instrumentation capabilities belonging inside the AgentOBS SDK itself (i.e., direct dependencies on AgentOBS that instrument application code at runtime).  
**Date:** March 2026

---

## Gap Analysis: What Exists vs What Is Missing

The SDK currently contains:
- **Event schemas/namespaces**: `trace`, `cost`, `cache`, `fence`, `guard`, `redact`, `prompt`, `diff`, `eval`, `audit`, `template` — schemas only, no runtime engines
- **Framework integrations**: OpenAI, Anthropic, Groq, Ollama, Together, LangChain, CrewAI, LlamaIndex — model-level patching only
- **Tracer core**: `_tracer.py` — `span()`, `agent_run()`, `agent_step()` context managers; no `@trace()` function decorator
- **Engines**: `redact.py`, `signing.py`, `governance.py`, `validate.py` — complete

**Missing runtime engines (the 7 tools this plan covers):**

| # | Document Tool | Document ID | Priority | Status in SDK |
|---|---------------|-------------|----------|---------------|
| 1 | `@trace()` decorator + trace engine | llm-trace (06) | P1 HIGH | Partial (context managers only) |
| 2 | Cost calculation engine | llm-cost (07) | P2 HIGH | Partial (schemas + pricing table only) |
| 3 | Tool call inspector | llm-inspect (08) | P2 HIGH | Missing |
| 4 | Tool schema builder | toolsmith (15) | P3 MEDIUM | Missing |
| 5 | Retry and fallback engine | llm-retry (17) | P4 MEDIUM | Missing |
| 6 | Semantic cache engine | llm-cache (18) | P4 MEDIUM | Partial (schemas only) |
| 7 | SDK instrumentation linter | agentobs-lint (30) | P3 MEDIUM | Missing |

---

## Tool 1: `@trace()` Function Decorator — Full `llm-trace` Engine

### Document description
> Single decorator `@trace()` instruments any Python function as an agent span. Captures span start/end, tool calls, arguments, return values, model calls, branching, retries. Parent-child span relationships. Async-native. Exports to OTLP, Datadog, Grafana Tempo, Honeycomb, Jaeger.

### What already exists
- `agentobs/_tracer.py` — `Tracer` class with `span()`, `agent_run()`, `agent_step()` context managers
- `agentobs/_span.py` — `Span` and `SpanContextManager` classes
- `agentobs/_trace.py` — `Trace` collector
- Framework integrations — patch `create()` on model clients; emit span events

### What is missing
1. `@trace()` **function/method decorator** that wraps any callable as a span automatically
2. `@trace()` **async-native** variant
3. **Auto-capture of arguments and return values** into span payload
4. **Pytest fixtures** (`agentobs_tracer`, `captured_spans`) for test-time span assertions
5. **OTLP span export bridge** (translate AgentOBS spans to OpenTelemetry `Span` format)
6. **Automatic tool call interception** — hook into `_tracer.py` so any function tagged `tool=True` emits `llm.trace.agent.step` with tool args/return

### Files to create / modify

| File | Action | Purpose |
|------|--------|---------|
| `agentobs/trace.py` | Create | Public `@trace()` decorator and related helpers |
| `agentobs/_tracer.py` | Extend | Add `Tracer.trace()` method backing the decorator |
| `agentobs/testing.py` | Extend | `captured_spans` pytest fixture, `assert_span_emitted()` helper |
| `agentobs/export/otlp_bridge.py` | Create | Translate `Span` → OTLP `ReadableSpan` format |
| `agentobs/__init__.py` | Extend | Re-export `trace` from top-level |

### Public API

```python
# Function decorator form
from agentobs import trace

@trace(name="my-step", model="gpt-4o")
def call_llm(prompt: str) -> str: ...

@trace(name="async-step")
async def async_step(x: int) -> dict: ...

# Tool annotation (instructs inspector + toolsmith)
@trace(name="search", tool=True)
def search_web(query: str) -> list[str]: ...

# Pytest fixture
def test_llm_flow(captured_spans):
    run_agent()
    assert any(s.name == "my-step" for s in captured_spans)
```

### Events emitted
- `llm.trace.span.started`
- `llm.trace.span.completed`
- `llm.trace.span.failed`
- `llm.trace.agent.step` (for `tool=True` spans)

### Implementation steps
1. Add `agentobs/trace.py` with `trace()` returning a `_TraceDecorator` that wraps sync/async callables
2. `_TraceDecorator.__call__` opens a `SpanContextManager`, calls the wrapped function, closes span on return or exception
3. Capture `args`/`kwargs` into `span.attributes` (respecting redaction types)
4. Add `Tracer.trace(**kwargs)` method that builds and returns a `_TraceDecorator`
5. Extend `agentobs/testing.py` with `@pytest.fixture captured_spans` using `contextvar` interception
6. Add `agentobs/export/otlp_bridge.py` translating `Span.to_span_payload()` dict → OTLP `{name, spanId, traceId, startTimeUnixNano, endTimeUnixNano, attributes}` format

### Tests to add
- `tests/test_trace_decorator.py` — sync/async wrapping, exception propagation, span payload fields
- `tests/test_trace_pytest_fixtures.py` — `captured_spans` fixture, span assertion helpers
- `tests/test_otlp_bridge.py` — OTLP format compliance

---

## Tool 2: Cost Calculation Engine — Full `llm-cost`

### Document description
> Per-call, per-run, per-agent USD and token accounting with budget alerts and trend dashboards. Budget alerts via Slack/email/webhook. Rolling 7-day and 30-day dashboards in terminal. Cost attribution by team, project, environment, or custom tag.

### What already exists
- `agentobs/namespaces/cost.py` — `CostTokenRecordedPayload`, `CostSessionRecordedPayload`, `CostAttributedPayload` schemas
- `agentobs/integrations/_pricing.py` — per-model USD/token pricing table, `get_pricing()`, `list_models()`
- `agentobs/integrations/openai.py` — `normalize_response()` returns `(TokenUsage, ModelInfo, CostBreakdown)`
- `agentobs/metrics.py` — token/cost metric helpers

### What is missing
1. **`CostTracker`** — accumulates costs across a run/session; queryable via API
2. **Budget alert system** — fires callback/warning when threshold exceeded
3. **`emit_cost_event()`** — builds and dispatches `llm.cost.recorded` event
4. **`emit_cost_attributed()`** — tags cost to org/team/env/tag
5. **Session-level aggregation** — group `CostBreakdown` per span → per run → total
6. **`cost_summary()` CLI helper** — formatted terminal cost table

### Files to create / modify

| File | Action | Purpose |
|------|--------|---------|
| `agentobs/cost.py` | Create | `CostTracker`, `budget_alert()`, `emit_cost_event()`, `cost_summary()` |
| `agentobs/_span.py` | Extend | Auto-emit cost event when span closes with model + token data |
| `agentobs/config.py` | Extend | `budget_usd_per_run`, `budget_usd_per_day` config fields |
| `agentobs/__init__.py` | Extend | Re-export `CostTracker`, `budget_alert` |

### Public API

```python
from agentobs.cost import CostTracker, budget_alert

# Explicit tracking
tracker = CostTracker()
tracker.record(model="gpt-4o", input_tokens=500, output_tokens=200)
print(tracker.total_usd)          # 0.00425
print(tracker.breakdown_by_model) # {"gpt-4o": 0.00425}

# Budget alert hook
budget_alert(
    threshold_usd=1.00,
    on_exceeded=lambda summary: print(f"Budget hit: {summary}"),
)

# Auto-emit on span close (config-driven)
configure(auto_emit_cost=True, budget_usd_per_run=0.50)
```

### Events emitted
- `llm.cost.recorded` (per model call, with token breakdown)
- `llm.cost.attributed` (per run/session, tagged to org/env)

### Implementation steps
1. Create `agentobs/cost.py` with `CostTracker` dataclass: fields `_records: list[CostRecord]`, methods `record()`, `total_usd`, `breakdown_by_model`, `breakdown_by_tag`, `to_dict()`
2. Add `BudgetMonitor` class: holds `threshold_usd`, `on_exceeded` callback, checks after each `record()` call
3. Add module-level `budget_alert()` factory that registers a `BudgetMonitor` with the global tracker
4. Add `emit_cost_event(span, cost_breakdown)` that builds a `CostTokenRecordedPayload` and dispatches via `_stream._dispatch()`
5. Wire into `_span.py`: when `span.cost_usd is not None` and `config.auto_emit_cost is True`, call `emit_cost_event()` on span close
6. Add `budget_usd_per_run` and `budget_usd_per_day` to `AgentOBSConfig`

### Tests to add
- `tests/test_cost_tracker.py` — accumulation, breakdown, multi-model
- `tests/test_budget_alert.py` — threshold trigger, callback invocation
- `tests/test_cost_event_emission.py` — span close → cost event

---

## Tool 3: Tool Call Inspector — `llm-inspect`

### Document description
> Surfaces every tool call in an agent run: function name, arguments, return value, time taken. Verifies whether the model actually used the tool result or silently discarded it. Interactive terminal replay: step through a recorded agent run call by call. Diff tool calls between two runs.

### What already exists
Nothing. Tool call inspection is not currently in the SDK.

### What needs to be built
1. **`ToolCallRecord`** — captures function name, args dict, return value, duration_ms, was_result_used flag
2. **Tool call recording hook** — wraps functions decorated with `@trace(tool=True)` to capture all of the above
3. **"Discarded output" detector** — post-run heuristic: checks if tool return value appears in the next model call's context; flags if absent
4. **`InspectorSession`** — collects `ToolCallRecord`s for a single agent run
5. **`inspect_trace(jsonl_path)`** — load a JSONL trace file and reconstruct tool calls for replay

### Files to create

| File | Action | Purpose |
|------|--------|---------|
| `agentobs/inspect.py` | Create | `ToolCallRecord`, `InspectorSession`, `inspect_trace()` |
| `agentobs/trace.py` | Extend | `@trace(tool=True)` feeds `InspectorSession` |

### Public API

```python
from agentobs.inspect import InspectorSession, inspect_trace

# Runtime inspection
session = InspectorSession()
with tracer.agent_run("research") as run:
    session.attach(run)
    result = my_tool("query")

for call in session.tool_calls:
    print(call.name, call.duration_ms, call.was_result_used)

# Post-run replay from JSONL
calls = inspect_trace("events.jsonl", trace_id="01XXXX")
for call in calls:
    print(call)
```

### Events emitted
- `llm.trace.agent.step` with `tool_name`, `tool_args`, `tool_result`, `duration_ms` fields added to payload

### Implementation steps
1. Create `ToolCallRecord` dataclass: `name`, `args`, `result`, `duration_ms`, `span_id`, `trace_id`, `was_result_used: bool | None`
2. Create `InspectorSession`: `attach(run)` method hooks into span close events for spans where `attributes.tool=True`
3. Implement "used?" heuristic: after agent run completes, scan subsequent span input tokens for tool result content (string match); set `was_result_used` accordingly
4. Create `inspect_trace(path, trace_id)` — reads JSONL, filters `llm.trace.agent.step` events for given `trace_id`, reconstructs `ToolCallRecord` list
5. Add `__repr__` table formatter to `InspectorSession` for terminal display

### Tests to add
- `tests/test_inspect.py` — tool call recording, timing accuracy, was_result_used detection

---

## Tool 4: Tool Schema Builder — `toolsmith`

### Document description
> Python type annotations and docstrings become schema properties and descriptions automatically. Validates tool call arguments at runtime before executing. Generates schemas for all major providers from one definition.

### What already exists
Nothing in the SDK. All framework integrations hard-code their own tool schema formats.

### What needs to be built
1. **`@tool` decorator** — inspects function signature and docstring; generates schemas on decoration
2. **Schema generators** — OpenAI function calling, Anthropic tool use, LangChain `BaseTool` format
3. **Runtime argument validator** — calls `inspect.signature`-based validation before function execution
4. **`ToolRegistry`** — collects all `@tool`-decorated functions; queryable by name

### Files to create

| File | Action | Purpose |
|------|--------|---------|
| `agentobs/toolsmith.py` | Create | `@tool` decorator, `ToolRegistry`, `build_openai_schema()`, `build_anthropic_schema()` |

### Public API

```python
from agentobs.toolsmith import tool, ToolRegistry

registry = ToolRegistry()

@tool(registry=registry, description="Search the web for a query.")
def search_web(query: str, max_results: int = 5) -> list[str]:
    """Search the web."""
    ...

# Get provider schemas
openai_schema = registry.to_openai_tools()
# [{"type": "function", "function": {"name": "search_web", "description": "...",
#   "parameters": {"type": "object", "properties": {"query": {"type": "string"}, ...}}}}]

anthropic_schema = registry.to_anthropic_tools()
# [{"name": "search_web", "description": "...", "input_schema": {...}}]

# Runtime validation
result = registry.call("search_web", {"query": "llm tracing", "max_results": 3})
```

### Events emitted
- No events emitted by this module (schema generation utility)
- Integrates with `@trace(tool=True)` for runtime validation logging via existing span events

### Implementation steps
1. Implement `@tool` decorator: at decoration time, call `inspect.signature()` to extract parameters; parse docstring with `inspect.getdoc()`; build `ToolSchema` dataclass with `{name, description, parameters: dict}`
2. Build `_param_to_json_schema(param: inspect.Parameter)` — maps Python type annotations to JSON Schema types (str→"string", int→"integer", float→"number", bool→"boolean", list→"array", dict→"object", `Optional[X]`→nullable)
3. Implement `build_openai_schema(tool_schema)` → OpenAI function calling format
4. Implement `build_anthropic_schema(tool_schema)` → Anthropic tool use format
5. Implement `ToolRegistry.call(name, args_dict)` — looks up function, validates args against schema, calls function, raises `ToolValidationError` on schema mismatch
6. Support `@tool` without explicit registry via module-level default registry

### Tests to add
- `tests/test_toolsmith.py` — schema generation from annotations, OpenAI/Anthropic format correctness, runtime validation, optional params, nested types

---

## Tool 5: Retry and Fallback Engine — `llm-retry`

### Document description
> Exponential backoff for rate limits, timeouts, transient errors. Cross-provider fallback: gpt-4o fails → route to claude-3-5-sonnet or local model. Circuit breaker: stop retrying failing provider and route all traffic to fallback. Cost-aware routing.

### What already exists
Nothing. No retry logic exists in the SDK.

### What needs to be built
1. **`@retry()` decorator** — exponential backoff with configurable max_attempts, base_delay, backoff_factor
2. **Retryable exception classification** — identifies rate limit (429), timeout, transient 5xx errors
3. **`FallbackChain`** — ordered list of provider callables; tries each in order
4. **`CircuitBreaker`** — per-provider state machine: CLOSED → OPEN (after N failures) → HALF_OPEN (after cooldown)
5. **Cost-aware router** — given a set of provider options and a latency budget, selects cheapest that historically meets latency SLA

### Files to create

| File | Action | Purpose |
|------|--------|---------|
| `agentobs/retry.py` | Create | `@retry()`, `FallbackChain`, `CircuitBreaker`, `CostAwareRouter` |

### Public API

```python
from agentobs.retry import retry, FallbackChain, CircuitBreaker

# Simple retry decorator
@retry(max_attempts=3, base_delay=1.0, backoff=2.0, on=["RateLimitError"])
def call_openai(prompt: str) -> str: ...

# Cross-provider fallback
chain = FallbackChain([
    call_openai,                  # try first
    call_anthropic,               # fallback if OpenAI fails
    call_local_ollama,            # final fallback
])
result = chain("my prompt")

# Circuit breaker
breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)

@breaker
def call_openai(prompt: str) -> str: ...
```

### Events emitted
- `llm.trace.span.failed` — on each failed attempt (with `retry_attempt` in payload)
- Routing decision metadata added to span payload on fallback

### Implementation steps
1. Implement `@retry(max_attempts, base_delay, backoff, jitter, on)` decorator using `functools.wraps`; sync and async variants via `inspect.iscoroutinefunction`
2. Exception classifier `_is_retryable(exc, on_patterns)` — matches exception class name against `on` list; also auto-detects `RateLimitError`, `Timeout`, `APIStatusError` (HTTP 429/5xx)
3. Implement `FallbackChain.run(*args, **kwargs)`: iterate through callables; on exception advance to next; on final failure raise `AllProvidersFailedError`
4. Implement `CircuitBreaker` state machine: track `failure_count`, `last_failure_time`; `CLOSED` allows calls; `OPEN` raises `CircuitOpenError` immediately; `HALF_OPEN` allows one probe call
5. Implement `CostAwareRouter`: given `{provider: (cost_per_token, p95_latency_ms)}` table and `latency_budget_ms`, select provider minimising cost among those within latency budget
6. Wire emit_span_failed() calls on each retry attempt

### Tests to add
- `tests/test_retry.py` — backoff timing, exponential factor, success on 3rd attempt, async support
- `tests/test_fallback_chain.py` — fallback sequence, all-fail error, first-success short-circuit
- `tests/test_circuit_breaker.py` — state transitions, recovery timeout, half-open probe

---

## Tool 6: Semantic Cache Engine — `llm-cache`

### Document description
> Deduplicates near-identical prompts using embedding similarity. Configurable similarity threshold. Storage backends: in-memory, Redis, SQLite. TTL-based and manual cache invalidation. promptlock prompt changes can trigger cache invalidation automatically.

### What already exists
- `agentobs/namespaces/cache.py` — `CacheHitPayload`, `CacheMissPayload`, `CacheEvictedPayload`, `CacheWrittenPayload` schemas (event shapes only)

### What needs to be built
1. **`SemanticCache`** — main caching class with `get()` and `set()` methods
2. **Embedding similarity** — configurable embedder (default: hash-based for dev; pluggable for prod)
3. **Backend adapters** — `InMemoryBackend`, `SQLiteBackend`, `RedisBackend`
4. **TTL management** — per-entry TTL; background eviction
5. **`@cached()` decorator** — wraps LLM call functions transparently
6. **Event emission** — emits `llm.cache.hit/miss/evicted/written` via `_stream._dispatch()`

### Files to create

| File | Action | Purpose |
|------|--------|---------|
| `agentobs/cache.py` | Create | `SemanticCache`, `@cached()`, backend adapters, TTL management |

### Public API

```python
from agentobs.cache import SemanticCache, cached

# Direct API
cache = SemanticCache(
    backend="sqlite",          # "memory" | "sqlite" | "redis"
    similarity_threshold=0.92,
    ttl_seconds=3600,
    db_path="agentobs_cache.db",
)

result = cache.get("What is the capital of France?")
if result is None:
    result = call_llm("What is the capital of France?")
    cache.set("What is the capital of France?", result)

# Decorator form
@cached(threshold=0.95, ttl=3600)
def call_llm(prompt: str) -> str: ...

# Invalidate on prompt change (promptlock integration hook)
cache.invalidate_by_tag("prompt:summariser-v2")
```

### Events emitted
- `llm.cache.hit` — on similarity match above threshold
- `llm.cache.miss` — on no match
- `llm.cache.written` — when new entry is stored
- `llm.cache.evicted` — when TTL expires or manual invalidation

### Implementation steps
1. Define `CacheEntry` dataclass: `key`, `embedding`, `value`, `created_at`, `ttl_seconds`, `tags: list[str]`
2. Implement `_EmbeddingBackend` protocol: `embed(text: str) -> list[float]`; default implementation uses SHA-256 hash of normalised text as a 256-dim binary vector for dev use; accept external embedder via constructor
3. Implement `_similarity(a, b)` as cosine similarity; for hash backend use exact match
4. Implement `InMemoryBackend`: `dict[str, CacheEntry]` with LRU eviction at `max_size`
5. Implement `SQLiteBackend`: `CREATE TABLE IF NOT EXISTS cache (key TEXT, embedding BLOB, value TEXT, created_at REAL, ttl_seconds REAL, tags TEXT)`; use `sqlite3` stdlib
6. Implement `RedisBackend`: optional import of `redis`; store serialised `CacheEntry` with Redis TTL
7. Implement `SemanticCache.get(prompt)`: embed prompt; scan backend for cosine similarity ≥ threshold; emit `cache.hit` or `cache.miss`
8. Implement `SemanticCache.set(prompt, value, tags)`: store entry; emit `cache.written`
9. Implement `@cached()` decorator wrapping sync/async callables
10. Add `invalidate_by_tag(tag)` and `invalidate_all()` methods; emit `cache.evicted`

### Tests to add
- `tests/test_cache.py` — hit/miss, TTL expiry, similarity threshold, in-memory backend
- `tests/test_cache_sqlite.py` — persistence across instances, eviction
- `tests/test_cache_events.py` — event emission on hit/miss/write/evict

---

## Tool 7: SDK Instrumentation Linter — `agentobs-lint`

### Document description
> Static analysis for codebases using the AgentOBS SDK. Checks: missing required payload fields, bare strings in PII-sensitive positions (should be Redactable), unregistered event type strings, missing trace context propagation. Emits warnings for incomplete instrumentation.

### What already exists
Nothing. No static analysis tooling exists in the SDK.

### What needs to be built
1. **AST visitor** — walks Python AST to find AgentOBS SDK usage patterns
2. **Check CHK-L01** — `Event(...)` calls with missing required fields
3. **Check CHK-L02** — bare `str` passed to fields typed as `Redactable` (e.g., `actor_id`, `session_id` in PII context)  
4. **Check CHK-L03** — string literals used as `event_type` that are not registered in `EventType` enum
5. **Check CHK-L04** — `@trace()` function that makes model calls but has no `trace_id` in scope
6. **flake8 / ruff plugin entrypoints**

### Files to create

| File | Action | Purpose |
|------|--------|---------|
| `agentobs/lint/__init__.py` | Create | Package init |
| `agentobs/lint/_visitor.py` | Create | AST visitor base |
| `agentobs/lint/_checks.py` | Create | Individual check implementations |
| `agentobs/lint/_flake8.py` | Create | flake8 plugin entrypoint (`AO` prefix error codes) |
| `agentobs/lint/_ruff.py` | Create | ruff plugin manifest |
| `pyproject.toml` | Extend | Register `agentobs.lint._flake8:AgentOBSChecker` as flake8 entry point |

### Error codes

| Code | Severity | Description |
|------|----------|-------------|
| `AO001` | Error | `Event()` missing required field: `event_type`, `source`, or `payload` |
| `AO002` | Warning | Bare `str` passed where `Redactable` is expected |
| `AO003` | Warning | Unregistered event type string literal |
| `AO004` | Warning | Model call inside function without active trace context |
| `AO005` | Warning | `emit_*()` called outside of `agent_run()` / `agent_step()` context |

### Public API

```bash
# flake8
flake8 --select AO myapp/

# ruff  
ruff check --select AO myapp/

# Direct CLI
python -m agentobs.lint myapp/
# AO002 myapp/agent.py:42  Bare str passed to actor_id; use Redactable("...")
# AO004 myapp/agent.py:67  call_openai() makes model calls without trace context
```

### Implementation steps
1. Create `agentobs/lint/_visitor.py` with `AgentOBSVisitor(ast.NodeVisitor)`: tracks imports of `agentobs` symbols, current scope stack
2. Implement CHK-L01: in `visit_Call`, when `func` is `Event` or `emit_*`, check `keywords` for required names
3. Implement CHK-L02: resolve type annotation of each kwarg target; if annotation is `Redactable` and value is a `Constant` (str literal), emit `AO002`
4. Implement CHK-L03: collect all `EventType.*` values at import time; when a bare string is passed as `event_type` kwarg, check against known set
5. Implement CHK-L04: track `with tracer.span()` / `with tracer.agent_run()` scope depth; detect calls to `openai.chat.completions.create` outside any trace scope
6. Implement `agentobs/lint/_flake8.py` according to flake8 plugin protocol: `name`, `version`, `off_by_default`, `parse_options`, and `run()` generator
7. Register via `pyproject.toml` `[project.entry-points."flake8.extension"] AO = "agentobs.lint._flake8:AgentOBSChecker"`

### Tests to add
- `tests/test_lint.py` — all 5 checks with correct and incorrect code samples, flake8 plugin registration

---

## Implementation Order

```
Phase 0 (Foundation — unblocks everything)
  ↓
1. @trace() decorator (llm-trace)     ← P1, highest dependency value
  ↓
2. Cost engine (llm-cost)             ← P2, wires into span close via @trace
  ↓
3. Tool call inspector (llm-inspect)  ← P2, depends on @trace(tool=True)
  ↓
4. Tool schema builder (toolsmith)    ← P3, feeds into @trace(tool=True)
  ↓
5. Retry engine (llm-retry)           ← P4, standalone, no SDK dependency
6. Semantic cache (llm-cache)         ← P4, standalone schema
  ↓
7. Instrumentation linter (agentobs-lint)  ← P3, can be built in parallel
```

### Milestones

| Milestone | Tools | Target |
|-----------|-------|--------|
| M1 — Core instrumentation | `@trace()` + cost engine | Week 1–2 |
| M2 — Inspection + tooling | `llm-inspect` + `toolsmith` | Week 3–4 |
| M3 — Reliability layer | `llm-retry` + `llm-cache` | Week 5–6 |
| M4 — Dev tooling | `agentobs-lint` | Week 7–8 |

---

## Coverage Requirements

All new modules must maintain the existing **≥ 90% line coverage** requirement.  
New test files follow the existing pattern: `tests/test_<module>.py`.

Minimum test counts per tool:
- `@trace()` decorator: 20 tests (sync, async, nesting, exceptions, arg capture, fixtures)
- Cost engine: 15 tests
- Tool call inspector: 12 tests
- Toolsmith: 18 tests (type mapping coverage)
- Retry engine: 20 tests (state transitions, backoff timing)
- Semantic cache: 20 tests (backends, TTL, similarity)
- Lint checks: 15 tests (5 checks × correct + incorrect + edge)

---

## Cross-cutting Concerns

### Config additions required
```python
# agentobs/config.py — add to AgentOBSConfig
auto_emit_cost: bool = True          # emit llm.cost.recorded on span close
budget_usd_per_run: float | None = None
budget_usd_per_day: float | None = None
cache_backend: str = "memory"        # "memory" | "sqlite" | "redis"
cache_similarity_threshold: float = 0.92
cache_ttl_seconds: int = 3600
retry_max_attempts: int = 3
retry_base_delay: float = 1.0
```

### `__init__.py` exports to add
```python
# Top-level convenience exports
from agentobs.trace import trace                 # @trace() decorator
from agentobs.cost import CostTracker, budget_alert
from agentobs.inspect import InspectorSession, inspect_trace
from agentobs.toolsmith import tool, ToolRegistry
from agentobs.retry import retry, FallbackChain, CircuitBreaker
from agentobs.cache import SemanticCache, cached
```

### Exception classes to add to `agentobs/exceptions.py`
- `ToolValidationError` — raised by `toolsmith` on arg schema mismatch
- `CircuitOpenError` — raised by `CircuitBreaker` in OPEN state
- `AllProvidersFailedError` — raised by `FallbackChain` when all providers fail
- `CacheBackendError` — raised by `SemanticCache` on backend connectivity failure
