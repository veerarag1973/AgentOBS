# Quickstart

AgentOBS is the reference implementation of **RFC-0001 AGENTOBS** — the open
event-schema standard for observability of agentic AI systems. This page walks
you through creating your first event, signing an audit chain, and exporting to
OTLP — in under five minutes.

## Installation

```bash
pip install agentobs
```

> The PyPI distribution is **`agentobs`**; the import name is `agentobs`.

For optional features:

```bash
pip install "agentobs[jsonschema]"   # JSON Schema validation
pip install "agentobs[http]"         # Async OTLP/webhook export (httpx)
pip install "agentobs[pydantic]"     # Pydantic v2 model layer
pip install "agentobs[otel]"         # OTelBridgeExporter — TracerProvider integration
```

Python 3.9+ is required.

## Creating your first event

Every interaction with an LLM tool is represented as an `Event`.
The minimum required fields are `event_type`, `source`, and `payload`:

```python
from agentobs import Event, EventType

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="my-tool@1.0.0",
    payload={"span_name": "run_agent", "status": "ok", "duration_ms": 312},
)

print(event.event_id)        # 01JPXXXXXXXXXXXXXXXXXXXXXXXX  (auto-generated ULID)
print(event.schema_version)  # 2.0
print(event.to_json())       # compact JSON
```

### Full event with optional fields

```python
from agentobs import Event, EventType, Tags

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="my-tool@1.0.0",
    payload={"span_name": "chat_completion", "status": "ok"},
    org_id="org_01HX",
    team_id="team_engineering",
    trace_id="a" * 32,        # 32-char hex OpenTelemetry trace ID
    span_id="b" * 16,         # 16-char hex span ID
    tags=Tags(env="production", model="gpt-4o"),
)
```

## Typed namespace payloads

Use the typed payload dataclasses from `agentobs.namespaces` to get
field validation and IDE auto-complete for each event namespace:

```python
import dataclasses
from agentobs import Event, EventType
from agentobs.namespaces.trace import (
    SpanPayload, TokenUsage, ModelInfo, GenAISystem, GenAIOperationName
)

token_usage = TokenUsage(input_tokens=120, output_tokens=80, total_tokens=200)
model_info  = ModelInfo(system=GenAISystem.OPENAI, name="gpt-4o")

payload = SpanPayload(
    span_name="chat_completion",
    status="ok",
    duration_ms=250,
    token_usage=token_usage.to_dict(),
    model_info=model_info.to_dict(),
    operation=GenAIOperationName.CHAT,
)

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="llm-trace@1.0.0",
    payload=dataclasses.asdict(payload),
)
```

## HMAC signing and audit chains

Sign individual events or build a full tamper-evident chain:

```python
from agentobs import Event, EventType
from agentobs.signing import sign, verify, AuditStream

# --- Single event ---
event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="my-tool@1.0.0",
    payload={"span_name": "chat"},
)
signed = sign(event, org_secret="my-secret")
assert verify(signed, org_secret="my-secret")

# --- Audit chain ---
stream = AuditStream(org_secret="my-secret", source="my-tool@1.0.0")
for i in range(5):
    evt = Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="my-tool@1.0.0",
        payload={"index": i},
    )
    stream.append(evt)

result = stream.verify()
assert result.valid                   # cryptographically intact
assert result.tampered_count == 0     # nothing altered
assert result.gaps == []              # no deletions
```

## PII redaction

Mark sensitive fields and apply a policy before storing or exporting events:

```python
from agentobs import Event, EventType
from agentobs.redact import Redactable, RedactionPolicy, Sensitivity

policy = RedactionPolicy(min_sensitivity=Sensitivity.PII, redacted_by="policy:corp-v1")

event = Event(
    event_type=EventType.PROMPT_SAVED,
    source="promptlock@1.0.0",
    payload={
        "prompt_text": Redactable("User email: alice@example.com", Sensitivity.PII, {"email"}),
        "model": "gpt-4o",
    },
)
result = policy.apply(event)
# result.event.payload["prompt_text"] is now "[REDACTED]"
```

## Exporting events

```python
import asyncio
from agentobs import Event, EventType
from agentobs.export.jsonl import JSONLExporter

exporter = JSONLExporter("events.jsonl")
events = [
    Event(event_type=EventType.TRACE_SPAN_COMPLETED, source="tool@1.0.0", payload={"i": i})
    for i in range(10)
]
asyncio.run(exporter.export_batch(events))
```

See [user_guide/export.md](user_guide/export.md) for OTLP, webhook, and `OTelBridgeExporter` (TracerProvider integration).

---

## Trace API (new in 2.0)

`start_trace()` gives you a first-class `Trace` object that tracks all spans
inside a single agent run. It works with both `with` and `async with`:

```python
import agentobs

agentobs.configure(exporter="console", service_name="my-agent")

with agentobs.start_trace("research-agent") as trace:
    with trace.llm_call("gpt-4o", temperature=0.7) as span:
        result = call_llm(prompt)
        span.set_token_usage(input=512, output=200, total=712)
        span.set_status("ok")
        span.add_event("reasoning_complete", {"steps": 3})

    with trace.tool_call("web_search") as span:
        output = run_search("latest AI news")
        span.set_status("ok")

# Pretty-print the span tree
trace.print_tree()
# — Agent Run: research-agent  [1.2s]
#  ├─ LLM Call: gpt-4o  [0.8s]  in=512 out=200 tokens  $0.0034
#  └─ Tool Call: web_search  [0.4s]  ok

# Summary statistics
print(trace.summary())
# {'trace_id': '...', 'agent_name': 'research-agent', 'span_count': 3,
#  'llm_calls': 1, 'tool_calls': 1, 'total_cost_usd': 0.0034, 'errors': 0}
```

---

## Lifecycle hooks (new in 2.0)

Register callbacks that fire on every span of a given type, globally:

```python
import agentobs

@agentobs.hooks.on_llm_call
def log_llm(span):
    print(f"LLM: {span.model}  temp={span.temperature}")

@agentobs.hooks.on_tool_call
def log_tool(span):
    print(f"Tool: {span.name}")
```

---

## Aggregating metrics (new in 2.0)

```python
import agentobs
from agentobs.stream import EventStream

events = list(EventStream.from_file("events.jsonl"))
m = agentobs.metrics.aggregate(events)

print(f"Success rate : {m.agent_success_rate:.0%}")
print(f"p95 LLM      : {m.llm_latency_ms.p95:.0f} ms")
print(f"Total cost   : ${m.total_cost_usd:.4f}")
print(f"By model     : {m.cost_by_model}")
```

---

## Semantic cache (new in 1.0.7)

Wrap any LLM function with `@cached` to skip the model entirely when a
semantically similar prompt was recently answered:

```python
from agentobs.cache import cached, SQLiteBackend

@cached(
    threshold=0.92,          # cosine similarity cutoff
    ttl=3600,                # seconds
    backend=SQLiteBackend("cache.db"),
    emit_events=True,        # emits llm.cache.hit/miss/written events
)
async def ask(prompt: str) -> str:
    return await my_llm(prompt)

# First call: cache miss → LLM runs
reply1 = await ask("Summarise the AgentOBS RFC.")

# Second call with a semantically near-identical prompt: instant cache hit
reply2 = await ask("Give me a short summary of the AgentOBS RFC.")
```

See the full [Semantic Cache user guide](user_guide/cache.md) and
[agentobs.cache API reference](api/cache.md).

---

## Lint your instrumentation (new in 1.0.7)

`agentobs.lint` scans Python files for instrumentation mistakes — missing
required fields, bare PII strings, LLM calls outside span contexts, etc.:

```python
from agentobs.lint import run_checks

errors = run_checks(open("myapp/pipeline.py").read(), "myapp/pipeline.py")
for e in errors:
    print(f"{e.filename}:{e.line}:{e.col}: {e.code} {e.message}")
```

Or run the CLI over a whole directory:

```bash
python -m agentobs.lint myapp/
# AO001  Event() missing required field 'payload'    pipeline.py:17
# AO004  LLM call outside tracer span context        pipeline.py:53
# 2 errors in 1 file.
```

The five AO-codes also appear in standard `flake8` / `ruff` output with no
extra configuration after installing `agentobs`.

See the full [Linting user guide](user_guide/linting.md) and
[agentobs.lint API reference](api/lint.md).

---

## Next steps

- [User Guide](user_guide/index.md) — in-depth guide to all features
- [Tracing API](user_guide/tracing.md) — `Trace`, `start_trace()`, async spans, `add_event()`
- [Debugging & Visualization](user_guide/debugging.md) — `print_tree()`, `summary()`, `visualize()`
- [Metrics & Analytics](user_guide/metrics.md) — `metrics.aggregate()`, `TraceStore`
- [Semantic Cache](user_guide/cache.md) — `SemanticCache`, `@cached`, backends
- [Linting & Static Analysis](user_guide/linting.md) — AO001–AO005, flake8 plugin, CI setup
- [API Reference](api/index.md) — full API reference
- [Namespace Payload Catalogue](namespaces/index.md) — typed payload catalogue
- [CLI](cli.md) — `agentobs check-compat` command
