# agentobs._hooks

Global span lifecycle hook registry.

---

## `hooks`

Module-level singleton `HookRegistry`. Import and use directly:

```python
import agentobs

@agentobs.hooks.on_llm_call
def my_hook(span):
    print(f"LLM called: {span.model}  temp={span.temperature}")

@agentobs.hooks.on_tool_call
def log_tool(span):
    if span.status == "error":
        alert(f"Tool failed: {span.name}")
```

---

## `HookRegistry`

```python
class HookRegistry:
    ...
```

Thread-safe (uses `threading.RLock`) registry of callbacks that fire when
spans of specific types are opened or closed.

### Decorator API

| Decorator | Fires |
|---|---|
| `@hooks.on_agent_start` | When an `agent_run` span opens (in `__enter__`) |
| `@hooks.on_agent_end` | When an `agent_run` span closes (in `__exit__`) |
| `@hooks.on_llm_call` | When an LLM span closes |
| `@hooks.on_tool_call` | When a tool span closes |

Each decorator registers the wrapped callable and returns it unchanged, so
it can be used as a plain function too.

```python
@agentobs.hooks.on_llm_call
def record_cost(span):
    budget.deduct(span.cost_usd or 0)
```

### `hooks.clear() -> None`

Unregister all hooks in all categories. Intended for test teardown:

```python
def teardown():
    agentobs.hooks.clear()
```

---

## Hook function signature

Every hook receives the active `Span` object as its only argument. The span
is **mutable** at call time — you can read or write attributes, but avoid
expensive synchronous I/O inside hooks because they run on the calling thread.

```python
from agentobs._span import Span

def my_hook(span: Span) -> None:
    # read fields
    print(span.name, span.model, span.status, span.error_category)
```

---

## Re-exports

```python
from agentobs import hooks, HookRegistry
from agentobs._hooks import hooks, HookRegistry
```
