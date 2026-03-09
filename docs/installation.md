# Installation

[![PyPI](https://img.shields.io/pypi/v/agentobs?color=4c8cbf&logo=pypi&logoColor=white)](https://pypi.org/project/agentobs/)

## Requirements

- Python **3.9** or later
- No required third-party dependencies for core event creation

## Install from PyPI

```bash
pip install agentobs
```

> The PyPI distribution is named **`agentobs`**. The Python import name remains `agentobs`.

## Optional extras

| Extra | Install command | What it enables |
|-------|-----------------|----------------|
| `jsonschema` | `pip install "agentobs[jsonschema]"` | `validate_event` with full JSON Schema validation |
| `openai` | `pip install "agentobs[openai]"` | `agentobs.integrations.openai` — auto-instruments the OpenAI Python SDK via `patch()` / `unpatch()`; includes `_pricing` module (USD/1M-token table for all current OpenAI models) |
| `http` | `pip install "agentobs[http]"` | `OTLPExporter` and `WebhookExporter` (stdlib transport; reserved for future `httpx` upgrade) |
| `pydantic` | `pip install "agentobs[pydantic]"` | `agentobs.models` — Pydantic v2 model layer, `model_json_schema()` |
| `otel` | `pip install "agentobs[otel]"` | `OTelBridgeExporter` — emits events through any configured `TracerProvider` (`opentelemetry-sdk>=1.24`) |
| `kafka` | `pip install "agentobs[kafka]"` | `EventStream.from_kafka()` via `kafka-python>=2.0` |
| `langchain` | `pip install "agentobs[langchain]"` | `LLMSchemaCallbackHandler` via `langchain-core>=0.2` |
| `llamaindex` | `pip install "agentobs[llamaindex]"` | `LLMSchemaEventHandler` via `llama-index-core>=0.10` |
| `crewai` | `pip install "agentobs[crewai]"` | `AgentOBSCrewAIHandler` and `patch()` via `crewai>=0.28` |
| `datadog` | `pip install "agentobs[datadog]"` | `DatadogExporter` (stdlib transport; reserved for future `ddtrace` integration) |
| `all` | `pip install "agentobs[all]"` | All optional extras |

Install all optional extras at once:

```bash
pip install "agentobs[all]"
```

## Development installation

```bash
git clone https://github.com/veerarag1973/agentobs.git
cd agentobs
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"   # or: pip install agentobs[all] for end-users
```

This installs all development dependencies including pytest, ruff, mypy, and
all optional extras.

## Verify the installation

```python
import agentobs  # pip install agentobs  →  import agentobs
print(agentobs.__version__)   # 1.0.8
print(agentobs.SCHEMA_VERSION)  # 2.0

from agentobs import Event, EventType
evt = Event(
    event_type=EventType.TRACE_SPAN_COMPLETED,
    source="smoke-test@1.0.0",
    payload={"ok": True},
)
evt.validate()
print("Installation OK")
```
