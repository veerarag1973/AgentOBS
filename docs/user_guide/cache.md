# Semantic Cache

> **Module:** `agentobs.cache`  
> **Added in:** 1.0.7

The semantic cache engine sits between your application code and the LLM.
Instead of forwarding every prompt to the model, it first checks whether a
*semantically similar* prompt was recently asked and, if so, returns the
cached answer immediately.

Benefits:

- **Reduced cost** — cache hits spend zero tokens.
- **Lower latency** — cache lookups take microseconds, not seconds.
- **Automatic observability** — every hit, miss, write, and eviction emits a
  structured `llm.cache.*` event that flows through the normal AgentOBS
  export pipeline.

---

## Choosing a backend

| Backend | Best for | Dependency |
|---------|----------|------------|
| `InMemoryBackend` | Dev, tests, single-process apps | None |
| `SQLiteBackend` | Single-server persistent cache | None (stdlib `sqlite3`) |
| `RedisBackend` | Multi-process / containerised apps | `pip install redis` |

---

## Using `SemanticCache` directly

```python
from agentobs.cache import SemanticCache, SQLiteBackend

cache = SemanticCache(
    backend=SQLiteBackend("cache.db"),
    similarity_threshold=0.92,
    ttl_seconds=86_400,   # 24 h
    namespace="summarisation",
    emit_events=True,
)

prompt = "Summarise the AgentOBS RFC in two sentences."

# Try the cache first
result = cache.get(prompt)

if result is None:
    # Cache miss — call the model
    result = await my_llm(prompt)
    # Store result for future similar prompts
    cache.set(prompt, result, tags=["summaries"])

print(result)
```

---

## Using `@cached` on a function

The `@cached` decorator is the simplest way to add semantic caching to an
existing function.  The function's first `str` argument (or a keyword
argument named `prompt`, `query`, `text`, or `message`) is used as the
cache key.

### Bare decorator (default settings)

```python
from agentobs.cache import cached

@cached
async def summarise(prompt: str) -> str:
    return await my_llm(prompt)

# First call: cache miss → LLM call → result stored
reply1 = await summarise("Explain the AgentOBS RFC.")

# Second call with a semantically near-identical prompt: cache hit → instant reply
reply2 = await summarise("Can you explain the AgentOBS RFC to me?")
```

### Custom settings

```python
from agentobs.cache import cached, SQLiteBackend

@cached(
    threshold=0.95,          # stricter similarity requirement
    ttl=3600,
    namespace="chat",
    backend=SQLiteBackend("prod.db"),
    tags=["production"],
    emit_events=True,
)
async def chat(message: str) -> str:
    return await my_llm(message)
```

### Sync functions

`@cached` works on regular `def` functions too:

```python
@cached(threshold=0.9, ttl=60)
def classify(text: str) -> str:
    return classifier.predict(text)
```

---

## Tag-based invalidation

Tags let you invalidate groups of cache entries without flushing the whole
namespace:

```python
cache = SemanticCache(namespace="docs")

# Write entries with tags
cache.set("What is a span?",   result_a, tags=["v1", "tracing"])
cache.set("What is a trace?",  result_b, tags=["v1", "tracing"])
cache.set("How do I install?", result_c, tags=["v1", "install"])

# Later, when v1 docs are retired:
removed = cache.invalidate_by_tag("v1")
print(f"Removed {removed} stale entries")
```

---

## Observability events

Every cache operation emits a structured event when `emit_events=True`:

```python
from agentobs import Event
from agentobs.namespaces.cache import CacheHitPayload

# The library emits these automatically — you do not construct them yourself.
# They appear in your normal event export (JSONL, OTLP, Datadog, etc.):

# llm.cache.hit  — similarity_score, ttl_remaining_seconds, tokens_saved, …
# llm.cache.miss — best_similarity_score, similarity_threshold, …
# llm.cache.written — ttl_seconds, response_token_count, …
# llm.cache.evicted — eviction_reason, entry_age_seconds, …
```

Use `agentobs.metrics.aggregate()` to turn these events into cost-savings
summaries:

```python
from agentobs.stream import EventStream
import agentobs

events = list(EventStream.from_file("events.jsonl"))
summary = agentobs.metrics.aggregate(events)
print(f"Cache hit rate: {summary.cache_hit_rate:.1%}")
print(f"Tokens saved:   {summary.cache_tokens_saved:,}")
```

---

## Using RedisBackend in production

```python
from agentobs.cache import SemanticCache, RedisBackend

# pip install redis
cache = SemanticCache(
    backend=RedisBackend(
        host="redis.internal",
        port=6379,
        db=2,
        prefix="agentobs:prod:",
    ),
    similarity_threshold=0.92,
    ttl_seconds=3600,
    namespace="chat",
)
```

Multiple application instances (e.g., containers behind a load balancer)
all share the same cache — a cache miss on one instance won't repeat work
done by another.

---

## Testing with the cache

Use `InMemoryBackend` (the default) in tests so no disk or network is involved:

```python
from agentobs.cache import SemanticCache, InMemoryBackend
from agentobs.testing import capture_events

cache = SemanticCache(backend=InMemoryBackend(), emit_events=True)

with capture_events() as events:
    cache.set("hello", "world")
    result = cache.get("hello")

assert result == "world"
hit_events = [e for e in events if e.event_type == "llm.cache.hit"]
assert len(hit_events) == 1
```

---

## Error handling

All backend failures are wrapped in `CacheBackendError`:

```python
from agentobs.cache import SemanticCache, RedisBackend, CacheBackendError

cache = SemanticCache(backend=RedisBackend(host="redis.internal"))

try:
    result = cache.get(prompt)
except CacheBackendError as exc:
    # Backend unavailable — fall through to the real LLM call
    result = None
```

---

## See also

- [API reference — agentobs.cache](../api/cache.md)
- [Namespace reference — llm.cache.*](../namespaces/cache.md)
- [agentobs.cost](../api/cost.md) — track money saved by cache hits
- [agentobs.retry](../api/retry.md) — combine caching with retry / fallback
