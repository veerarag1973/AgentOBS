# llm.trace — Span and Agent Trace

> **Auto-documented module:** `tracium.namespaces.trace`

The `llm.trace.*` namespace contains payload dataclasses for recording
individual LLM calls, agent steps, and full agent runs (RFC-0001 §8).

## Payload classes

| Class | Event type | Description |
|-------|-----------|-------------|
| `SpanPayload` | `llm.trace.span.completed` | Single unit of LLM work — model call, tool invocation, or sub-agent call |
| `AgentStepPayload` | `llm.trace.agent.step` | One iteration of a multi-step agent loop |
| `AgentRunPayload` | `llm.trace.agent.completed` | Root summary for a complete agent run |

## SpanPayload — key fields

| Field | Type | Description |
|-------|------|-------------|
| `span_name` | `str` | Human-readable name for the span |
| `status` | `str` | `"ok"`, `"error"`, or `"timeout"` |
| `duration_ms` | `float` | End-to-end latency in milliseconds |
| `token_usage` | `dict \| None` | Serialised `TokenUsage` (fields: `input_tokens`, `output_tokens`, `total_tokens`) |
| `model_info` | `dict \| None` | Serialised `ModelInfo` (fields: `system`, `name`) |
| `finish_reason` | `str \| None` | Provider finish reason (`"stop"`, `"length"`, `"tool_calls"`…) |
| `stream` | `bool` | Whether the response was streamed |

## Value objects

**`TokenUsage`** — token counts aligned with OTel `gen_ai.usage.*` semconv:

| Field | Type | Description |
|-------|------|-------------|
| `input_tokens` | `int` | Tokens consumed by the prompt |
| `output_tokens` | `int` | Tokens produced in the completion |
| `total_tokens` | `int \| None` | Sum (or provider-reported total) |

**`ModelInfo`** — model identity:

| Field | Type | Description |
|-------|------|-------------|
| `system` | `GenAISystem` | Provider enum value (e.g. `GenAISystem.OPENAI`) |
| `name` | `str` | Model identifier (e.g. `"gpt-4o"`) |

## Example

```python
from tracium import Event, EventType
from tracium.namespaces.trace import (
    SpanPayload, TokenUsage, ModelInfo, GenAISystem
)

token_usage = TokenUsage(input_tokens=512, output_tokens=128, total_tokens=640)
model_info  = ModelInfo(system=GenAISystem.OPENAI, name="gpt-4o")

payload = SpanPayload(
    span_name="chat_completion",
    status="ok",
    duration_ms=340.5,
    token_usage=token_usage.to_dict(),
    model_info=model_info.to_dict(),
    finish_reason="stop",
    stream=False,
)

event = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="my-app@1.0.0",
    org_id="org_01HX",
    payload=payload.to_dict(),
)
```
