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

### Sync decorator API

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

### Async decorator API

Async variants fire their coroutine via `asyncio.ensure_future()` on the
running event loop. They are silently skipped when no loop is running.

| Decorator | Fires |
|---|---|
| `@hooks.on_agent_start_async` | When an `agent_run` span opens |
| `@hooks.on_agent_end_async` | When an `agent_run` span closes |
| `@hooks.on_llm_call_async` | When an LLM span closes |
| `@hooks.on_tool_call_async` | When a tool span closes |

```python
@agentobs.hooks.on_agent_start_async
async def record_start(span):
    await db.record_agent_start(span.span_id)

@agentobs.hooks.on_llm_call_async
async def async_cost_tracker(span):
    await budget.async_deduct(span.cost_usd or 0)
```

The `AsyncHookFn` type alias is exported for type annotations:

```python
from agentobs import AsyncHookFn
from agentobs._span import Span
import asyncio

async def my_async_hook(span: Span) -> None: ...

fn: AsyncHookFn = my_async_hook
```

### `hooks.clear() -> None`

Unregister all hooks (sync and async) in all categories. Intended for test
teardown:

```python
def teardown():
    agentobs.hooks.clear()
```

---

## Hook function signatures

**Sync:** `(span: Span) -> None`  
**Async:** `(span: Span) -> Coroutine[Any, Any, None]`

The span is **readable** at call time. Avoid expensive synchronous blocking
I/O in sync hooks — they run on the calling thread.

```python
from agentobs._span import Span

def my_hook(span: Span) -> None:
    print(span.name, span.model, span.status, span.error_category)

async def my_async_hook(span: Span) -> None:
    await some_async_operation(span.span_id)
```

---

## Re-exports

```python
from agentobs import hooks, HookRegistry, AsyncHookFn
from agentobs._hooks import hooks, HookRegistry, AsyncHookFn
```
