# agentobs.auto

Integration auto-discovery. Detects and patches every installed LLM
integration in one call — no per-library import required.

---

## Overview

`agentobs.auto` inspects `sys.modules` and the installed package set to find
all LLM client libraries supported by AgentOBS integrations, then calls
`patch()` on each one that is present.

> **Important:** `import agentobs.auto` alone does **not** patch anything.
> You must call `agentobs.auto.setup()` explicitly.

---

## `setup()`

```python
def setup(*, verbose: bool = False) -> set[str]:
```

Auto-patch every installed and importable LLM integration.

Currently supports: `openai`, `anthropic`, `groq`, `ollama`, `together`.

Returns the set of integration names that were successfully patched.

```python
import agentobs
import agentobs.auto

agentobs.configure(exporter="console", service_name="my-agent")
patched = agentobs.auto.setup()
# patched == {"openai", "anthropic"}  (whichever are installed)
```

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `verbose` | `bool` | `False` | If `True`, logs each patched integration to `logging.getLogger("agentobs.auto")` at `INFO` level. |

**Returns:** `set[str]` — names of successfully patched integrations.

---

## `teardown()`

```python
def teardown(*, verbose: bool = False) -> set[str]:
```

Unpatch all integrations that were patched by `setup()`. Safe to call even
if `setup()` was not called.

Returns the set of integration names that were successfully unpatched.

```python
agentobs.auto.teardown()
# All patched integrations restored to their original state
```

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `verbose` | `bool` | `False` | If `True`, logs each unpatched integration at `INFO` level. |

**Returns:** `set[str]` — names of successfully unpatched integrations.

---

## Typical usage pattern

```python
import agentobs
import agentobs.auto

# --- application startup ---
agentobs.configure(
    exporter="console",
    service_name="my-agent",
    schema_version="2.0",
)
agentobs.auto.setup(verbose=True)

# All LLM calls from this point forward are automatically instrumented

# --- application shutdown (optional) ---
agentobs.auto.teardown()
```

### Test isolation

```python
import agentobs.auto

def setup_method(self):
    agentobs.auto.setup()

def teardown_method(self):
    agentobs.auto.teardown()
```

---

## Re-exports

```python
import agentobs.auto

agentobs.auto.setup
agentobs.auto.teardown
```
