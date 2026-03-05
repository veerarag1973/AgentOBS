# tracium.integrations

Lightweight adapters for third-party LLM orchestration frameworks.

Each integration is a **soft dependency** — the framework is only required when
you actually instantiate the handler. All adapters are importable lazily via the
`tracium.integrations` package without triggering an import error if
the underlying framework is not installed.

---

## `tracium.integrations.langchain` — LangChain

### Installation

```bash
pip install "tracium[langchain]"
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

LangChain callback handler that emits `tracium` events as LangChain
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
from tracium.integrations.langchain import LLMSchemaCallbackHandler

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

## `tracium.integrations.llamaindex` — LlamaIndex

### Installation

```bash
pip install "tracium[llamaindex]"
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
`tracium` events.

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
from tracium.integrations.llamaindex import LLMSchemaEventHandler

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

Both handlers are accessible via module attribute access on
`tracium.integrations` without importing the sub-module explicitly:

```python
import tracium.integrations as integrations

# Equivalent to: from tracium.integrations.langchain import ...
Handler = integrations.LLMSchemaCallbackHandler

# Equivalent to: from tracium.integrations.llamaindex import ...
Handler = integrations.LLMSchemaEventHandler
```
