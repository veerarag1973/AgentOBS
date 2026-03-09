# agentobs.cache — Semantic Cache Engine

> **Module:** `agentobs.cache`  
> **Added in:** 1.0.7

The semantic cache engine deduplicates LLM calls by comparing the cosine
similarity of incoming prompts to previously cached prompts. When a prompt
is *similar enough* (controlled by `similarity_threshold`) the cached
response is returned immediately — no model call, no tokens spent.

All public names are re-exported from `agentobs.cache` and from the top-
level `agentobs` namespace.

---

## Quick example

```python
from agentobs.cache import SemanticCache, InMemoryBackend, cached

# --- option A: explicit cache object ---
cache = SemanticCache(
    backend=InMemoryBackend(max_size=512),
    similarity_threshold=0.92,
    ttl_seconds=3600,
    namespace="my-app",
    emit_events=True,
)
cached_value = cache.get("What is AgentOBS?")
if cached_value is None:
    result = await my_llm_call(prompt)
    cache.set("What is AgentOBS?", result)

# --- option B: @cached decorator ---
@cached(threshold=0.92, ttl=3600, emit_events=True)
async def ask(prompt: str) -> str:
    return await my_llm_call(prompt)
```

---

## `SemanticCache`

```python
class SemanticCache(
    backend: CacheBackend | None = None,
    similarity_threshold: float = 0.92,
    ttl_seconds: int = 3600,
    namespace: str = "default",
    embedder: Callable[[str], list[float]] | None = None,
    max_size: int = 1024,
    emit_events: bool = True,
)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `backend` | `CacheBackend \| None` | `None` | Storage backend; if `None` an `InMemoryBackend(max_size)` is created automatically |
| `similarity_threshold` | `float` | `0.92` | Minimum cosine similarity to count as a cache hit; range `[0.0, 1.0]` |
| `ttl_seconds` | `int` | `3600` | Seconds before a cache entry is considered stale |
| `namespace` | `str` | `"default"` | Logical partition; entries from different namespaces never collide |
| `embedder` | `Callable[[str], list[float]] \| None` | `None` | Custom embedding function; defaults to a lightweight built-in TF-IDF encoder |
| `max_size` | `int` | `1024` | Maximum capacity when creating the default `InMemoryBackend` |
| `emit_events` | `bool` | `True` | Emit `llm.cache.*` events on every hit, miss, write, or eviction |

### Methods

#### `SemanticCache.get(prompt)`

```python
def get(self, prompt: str) -> str | None
```

Compute the embedding for `prompt`, search for the nearest cached entry, and
return the cached response string if the similarity is at or above the
threshold. Returns `None` on a miss. Emits `llm.cache.hit` or
`llm.cache.miss` when `emit_events=True`.

#### `SemanticCache.set(prompt, value, tags=None)`

```python
def set(self, prompt: str, value: str, tags: list[str] | None = None) -> None
```

Store `value` in the backend keyed by the embedding of `prompt`. Optional
`tags` can be used to group entries for bulk invalidation.  Emits
`llm.cache.written` when `emit_events=True`.

#### `SemanticCache.invalidate_by_tag(tag)`

```python
def invalidate_by_tag(self, tag: str) -> int
```

Remove all entries whose tag list contains `tag`. Returns the number of
entries removed. Each removal emits `llm.cache.evicted` with
`eviction_reason="manual_invalidation"` when `emit_events=True`.

#### `SemanticCache.invalidate_all()`

```python
def invalidate_all(self) -> int
```

Flush the entire namespace. Returns the number of entries removed.

---

## `@cached` decorator

```python
from agentobs.cache import cached
```

The `@cached` decorator is available in **bare** and **with-arguments** forms.

### Bare form

```python
@cached
async def ask(prompt: str) -> str: ...
```

Uses default `SemanticCache` settings: `threshold=0.92`, `ttl=3600`,
`namespace="default"`, `InMemoryBackend`.

### With-arguments form

```python
@cached(
    threshold: float = 0.92,
    ttl: int = 3600,
    namespace: str = "default",
    backend: CacheBackend | None = None,
    tags: list[str] | None = None,
    emit_events: bool = True,
)
async def ask(prompt: str) -> str: ...
```

### How the cache key is derived

The first positional `str` argument (or the keyword argument named `prompt`,
`query`, `text`, or `message`) is used as the cache key. If no qualifying
argument is found, the entire `repr(args, kwargs)` string is used.

### Sync support

`@cached` works on both `def` and `async def` functions. For sync functions
the cache get/set operations are performed synchronously in the calling
thread.

---

## Backend classes

### `InMemoryBackend`

```python
class InMemoryBackend(max_size: int = 1024)
```

LRU in-process store. Thread-safe. Data is lost when the process exits.  
Good for: development, tests, single-process deployments.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `max_size` | `int` | `1024` | Max entries before LRU eviction |

### `SQLiteBackend`

```python
class SQLiteBackend(db_path: str = "agentobs_cache.db")
```

Persistent store backed by stdlib `sqlite3`. No extra dependencies required.
Safe for multi-threaded access within a single process. Data survives process
restarts.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `db_path` | `str` | `"agentobs_cache.db"` | Filesystem path to the SQLite database file |

### `RedisBackend`

```python
class RedisBackend(
    host: str = "localhost",
    port: int = 6379,
    db: int = 0,
    prefix: str = "agentobs:",
)
```

Distributed store via the optional [`redis`](https://pypi.org/project/redis/)
package. Suitable for multi-process deployments, containers, or serverless
functions that share a Redis instance.

**Requires:** `pip install redis`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | `str` | `"localhost"` | Redis server hostname |
| `port` | `int` | `6379` | Redis server port |
| `db` | `int` | `0` | Redis logical database index |
| `prefix` | `str` | `"agentobs:"` | Key prefix; useful when sharing a Redis instance |

---

## `CacheEntry`

```python
@dataclass
class CacheEntry:
    key_hash: str
    value: str
    embedding: list[float]
    created_at: float          # Unix timestamp
    ttl_seconds: int
    namespace: str
    tags: list[str]
    similarity_score: float    # score from the lookup that produced this entry (hits only)
```

Returned by backend inspection methods. Not usually constructed by
application code.

---

## `CacheBackendError`

```python
class CacheBackendError(AgentOBSError):
    backend: str    # e.g. "SQLiteBackend"
    reason: str
```

Raised when a backend operation fails (disk full, Redis connection refused,
etc.). All backend errors are wrapped in `CacheBackendError` so callers can
handle them without importing backend-specific exception classes.

---

## Events emitted

When `emit_events=True` (the default), the following events are emitted
using the globally configured exporter:

| Event type | Payload class | Condition |
|-----------|---------------|-----------|
| `llm.cache.hit` | `CacheHitPayload` | Prompt similarity ≥ threshold |
| `llm.cache.miss` | `CacheMissPayload` | Prompt similarity < threshold or cache empty |
| `llm.cache.written` | `CacheWrittenPayload` | New entry stored |
| `llm.cache.evicted` | `CacheEvictedPayload` | Entry removed (TTL, LRU, or manual) |

Payload dataclasses are in `agentobs.namespaces.cache`.  See
[`docs/namespaces/cache.md`](../namespaces/cache.md) for field-by-field
documentation.

---

## See also

- [User guide — Semantic Cache](../user_guide/cache.md)
- [Namespace reference — llm.cache.*](../namespaces/cache.md)
- [`agentobs.cost`](cost.md) — track money saved by cache hits
- [`agentobs.retry`](retry.md) — combine caching with retry / fallback
