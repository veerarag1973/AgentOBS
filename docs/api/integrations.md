# agentobs.integrations

Lightweight adapters for third-party LLM orchestration frameworks and providers.

Each integration is a **soft dependency** — the framework/provider package is
only required when you actually call `patch()` or instantiate the handler.
All adapters are importable lazily via the `agentobs.integrations` package
without triggering an import error if the underlying package is not installed.

---

## `agentobs.integrations.openai` — OpenAI Auto-Instrumentation

### Installation

```bash
pip install "agentobs[openai]"
# or
pip install openai
```

### Overview

This module monkey-patches the OpenAI Python SDK so every
`client.chat.completions.create(...)` call (sync and async) automatically
populates the active `agentobs` span with:

- **`TokenUsage`** — `input_tokens`, `output_tokens`, `total_tokens`,
  `cached_tokens`, `reasoning_tokens`
- **`ModelInfo`** — `system=GenAISystem.OPENAI`, `name` from
  `response.model`
- **`CostBreakdown`** — USD cost computed from the static pricing table in
  `agentobs.integrations._pricing`

### `patch()`

```python
def patch() -> None
```

Wraps `Completions.create` (sync) and `AsyncCompletions.create` (async).
Idempotent — calling it multiple times has no effect.

**Raises:** `ImportError` if the `openai` package is not installed.

### `unpatch()`

```python
def unpatch() -> None
```

Restores the original OpenAI methods. Safe to call even if `patch()` was
never called.

**Raises:** `ImportError` if the `openai` package is not installed.

### `is_patched()`

```python
def is_patched() -> bool
```

Returns `True` if `patch()` has been called and not yet reverted.
Returns `False` if `openai` is not installed.

### `normalize_response(response)`

```python
def normalize_response(response: Any) -> tuple[TokenUsage, ModelInfo, CostBreakdown]
```

Extracts structured observability data from an OpenAI `ChatCompletion`
response object (or any duck-typed mock with the same attribute structure).

| OpenAI field | AgentOBS field |
|---|---|
| `response.model` | `ModelInfo.name` |
| `usage.prompt_tokens` | `TokenUsage.input_tokens` |
| `usage.completion_tokens` | `TokenUsage.output_tokens` |
| `usage.total_tokens` | `TokenUsage.total_tokens` |
| `usage.prompt_tokens_details.cached_tokens` | `TokenUsage.cached_tokens` |
| `usage.completion_tokens_details.reasoning_tokens` | `TokenUsage.reasoning_tokens` |

Returns a 3-tuple `(TokenUsage, ModelInfo, CostBreakdown)`.

### Example

```python
from agentobs.integrations import openai as openai_integration
import openai, agentobs

# One-time global setup
openai_integration.patch()

agentobs.configure(exporter="console", service_name="my-agent")
client = openai.OpenAI()

with agentobs.tracer.span("llm-call") as span:
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
    )
    # span.token_usage, span.cost, span.model auto-populated

# Restore original methods
openai_integration.unpatch()
```

### Async example

```python
import asyncio, openai, agentobs
from agentobs.integrations import openai as openai_integration

openai_integration.patch()
agentobs.configure(exporter="console", service_name="my-async-agent")

async def main():
    client = openai.AsyncOpenAI()
    with agentobs.tracer.span("async-llm-call") as span:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hi"}],
        )
        # span auto-populated

asyncio.run(main())
openai_integration.unpatch()
```

### Auto-populate behaviour

`_auto_populate_span()` is called internally after every patched `create()`.
It silently skips population if:

- No span is currently active on this thread / task.
- `span.token_usage` is already set (manual data is not overwritten).
- `normalize_response()` raises for any reason (e.g. malformed response).
  Instrumentation errors are **never propagated** to user code.

Model name is set on the span only if `span.model is None`.

---

## `agentobs.integrations._pricing` — OpenAI Pricing Table

Static pricing table (USD / 1 M tokens) for all current OpenAI models.
Prices reflect OpenAI's published rates as of **2026-03-04**.

### `PRICING_DATE`

```python
PRICING_DATE: str = "2026-03-04"
```

Snapshot date attached to every `CostBreakdown` for auditability.

### `get_pricing(model)`

```python
def get_pricing(model: str) -> dict[str, float] | None
```

Returns the pricing entry for `model`, or `None` if not in the table.
Performs an exact lookup first, then strips trailing date suffixes
(e.g. `"gpt-4o-2024-11-20"` → `"gpt-4o"`) to handle version-pinned names.

Returned dict has at minimum `"input"` and `"output"` (USD/1M tokens); may
also include `"cached_input"` and/or `"reasoning"` where applicable.

### `list_models()`

```python
def list_models() -> list[str]
```

Returns a sorted list of all model names in the pricing table.

### Supported models

| Model family | Models |
|---|---|
| GPT-4o | `gpt-4o`, `gpt-4o-2024-11-20`, `gpt-4o-2024-08-06`, `gpt-4o-2024-05-13` |
| GPT-4o mini | `gpt-4o-mini`, `gpt-4o-mini-2024-07-18` |
| GPT-4 Turbo | `gpt-4-turbo`, `gpt-4-turbo-2024-04-09`, `gpt-4-0125-preview`, `gpt-4-1106-preview` |
| GPT-4 base | `gpt-4`, `gpt-4-0613` |
| GPT-3.5 Turbo | `gpt-3.5-turbo`, `gpt-3.5-turbo-0125`, `gpt-3.5-turbo-1106` |
| o1 family | `o1`, `o1-2024-12-17`, `o1-mini`, `o1-mini-2024-09-12`, `o1-preview` |
| o3 family | `o3-mini`, `o3-mini-2025-01-31`, `o3` |
| Embeddings | `text-embedding-3-small`, `text-embedding-3-large`, `text-embedding-ada-002` |

---

## `agentobs.integrations.langchain` — LangChain

### Installation

```bash
pip install "agentobs[langchain]"
# or
pip install langchain-core
```

### `LLMSchemaCallbackHandler`

```python
class LLMSchemaCallbackHandler(BaseCallbackHandler):
    def __init__(
        self,
        source: str = "langchain",
        org_id: str = "",
        exporter: Optional[Exporter] = None,
    )
```

LangChain callback handler that emits `agentobs` events as LangChain
operations occur. Subclasses `langchain_core.callbacks.BaseCallbackHandler`
(or `langchain.callbacks.BaseCallbackHandler` for older LangChain versions).

Importing or instantiating this class raises `ImportError` if neither
`langchain_core` nor `langchain` is installed.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source` | `str` | `"langchain"` | Event source string attached to every emitted event. |
| `org_id` | `str` | `""` | Organisation ID propagated into event payloads. |
| `exporter` | `Exporter \| None` | `None` | Optional exporter. When set, each event is fire-and-forget exported via `loop.create_task()`. |

**Example:**

```python
from agentobs.integrations.langchain import LLMSchemaCallbackHandler

handler = LLMSchemaCallbackHandler(source="my-app@1.0.0", org_id="acme")

# Attach to any LangChain chain / agent
chain = my_chain.with_config({"callbacks": [handler]})
chain.invoke({"input": "Hello"})

# Inspect captured events
for event in handler.events:
    print(event.event_type, event.payload)
```

### Emitted event types

| LangChain callback | Event type emitted |
|-------------------|--------------------|
| `on_llm_start` | `llm.trace.span.started` |
| `on_llm_end` | `llm.trace.span.completed` |
| `on_llm_error` | `llm.trace.span.error` |
| `on_tool_start` | `llm.trace.tool_call.started` |
| `on_tool_end` | `llm.trace.tool_call.completed` |
| `on_tool_error` | `llm.trace.tool_call.error` |

### Methods

#### `events -> List[Event]` *(property)*

All events captured since the handler was created or last cleared.

#### `clear_events() -> None`

Clear the internal event list.

---

## `agentobs.integrations.llamaindex` — LlamaIndex

### Installation

```bash
pip install "agentobs[llamaindex]"
# or
pip install llama-index-core
```

### `LLMSchemaEventHandler`

```python
class LLMSchemaEventHandler:
    def __init__(
        self,
        source: str = "llamaindex",
        org_id: str = "",
        exporter: Optional[Exporter] = None,
    )
```

LlamaIndex callback event handler that converts LlamaIndex callback events to
`agentobs` events.

Importing or instantiating this class raises `ImportError` if neither
`llama_index.core` nor `llama_index` is installed.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source` | `str` | `"llamaindex"` | Event source string attached to every emitted event. |
| `org_id` | `str` | `""` | Organisation ID propagated into event payloads. |
| `exporter` | `Exporter \| None` | `None` | Optional exporter for fire-and-forget event delivery. |

**Example:**

```python
from llama_index.core import Settings
from agentobs.integrations.llamaindex import LLMSchemaEventHandler

handler = LLMSchemaEventHandler(source="my-app@1.0.0", org_id="acme")
Settings.callback_manager.add_handler(handler)
```

### Handled event types

| LlamaIndex event category | Event type emitted |
|--------------------------|-------------------|
| LLM events (`LLM`, `llm`) | `llm.trace.span.started` / `llm.trace.span.completed` |
| Function call events (`FUNCTION_CALL`) | `llm.trace.tool_call.started` / `llm.trace.tool_call.completed` |
| Query events (`QUERY`) | `llm.trace.query.started` / `llm.trace.query.completed` |

### Methods

#### `on_event_start(event_type, payload=None, event_id=None, parent_id=None) -> str`

Called by LlamaIndex at the start of a tracked operation. Returns the `event_id`.

#### `on_event_end(event_type, payload=None, event_id=None) -> None`

Called by LlamaIndex at the end of a tracked operation. Computes `duration_ms`
from the paired `on_event_start` call.

#### `start_trace(trace_id=None) -> None`

No-op — provided for LlamaIndex callback manager protocol compliance.

#### `end_trace(...) -> None`

No-op — provided for LlamaIndex callback manager protocol compliance.

---

## Lazy top-level imports

All handlers and the OpenAI integration helpers are accessible via module
attribute access on `agentobs.integrations` without importing the sub-module
explicitly:

```python
import agentobs.integrations as integrations

# OpenAI integration
integrations.patch()       # agentobs.integrations.openai.patch()
integrations.unpatch()
integrations.is_patched()
integrations.normalize_response(response)

# LangChain
Handler = integrations.LLMSchemaCallbackHandler

# LlamaIndex
Handler = integrations.LLMSchemaEventHandler
```
