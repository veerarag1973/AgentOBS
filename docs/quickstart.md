# Quickstart

AgentOBS is the reference implementation of **RFC-0001 AGENTOBS** — the open
event-schema standard for observability of agentic AI systems. This page walks
you through creating your first event, signing an audit chain, and exporting to
OTLP — in under five minutes.

## Installation

```bash
pip install agentobs
```

> The PyPI distribution is **`agentobs`**; the import name is `tracium`.

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
from tracium import Event, EventType

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
from tracium import Event, EventType, Tags

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

Use the typed payload dataclasses from `tracium.namespaces` to get
field validation and IDE auto-complete for each event namespace:

```python
import dataclasses
from tracium import Event, EventType
from tracium.namespaces.trace import (
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
from tracium import Event, EventType
from tracium.signing import sign, verify, AuditStream

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
from tracium import Event, EventType
from tracium.redact import Redactable, RedactionPolicy, Sensitivity

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
from tracium import Event, EventType
from tracium.export.jsonl import JSONLExporter

exporter = JSONLExporter("events.jsonl")
events = [
    Event(event_type=EventType.TRACE_SPAN_COMPLETED, source="tool@1.0.0", payload={"i": i})
    for i in range(10)
]
asyncio.run(exporter.export_batch(events))
```

See [user_guide/export.md](user_guide/export.md) for OTLP, webhook, and `OTelBridgeExporter` (TracerProvider integration).

## Next steps

- [User Guide](user_guide/index.md) — in-depth guide to all features
- [API Reference](api/index.md) — full API reference
- [Namespace Payload Catalogue](namespaces/index.md) — typed payload catalogue
- [CLI](cli.md) — `tracium check-compat` command
