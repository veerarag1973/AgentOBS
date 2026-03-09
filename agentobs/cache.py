"""Semantic cache engine — llm-cache (Tool 6).

Public API
----------
* :class:`SemanticCache` — main cache with get/set/invalidate methods
* :func:`cached` — decorator wrapping sync/async callables transparently
* :class:`InMemoryBackend` — in-process LRU-capped backend (default)
* :class:`SQLiteBackend` — file-persistent backend (stdlib sqlite3)
* :class:`RedisBackend` — Redis-backed store (requires ``redis`` extra)
* :class:`CacheBackendError` — raised on backend connectivity failures
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import functools
import hashlib
import inspect
import json
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar, runtime_checkable

__all__ = [
    "SemanticCache",
    "cached",
    "InMemoryBackend",
    "SQLiteBackend",
    "RedisBackend",
    "CacheBackendError",
    "CacheEntry",
]

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CacheBackendError(Exception):
    """Raised when a cache backend fails to connect or operate.

    Attributes
    ----------
    backend:
        Short identifier for the backend (``"memory"``, ``"sqlite"``, ``"redis"``).
    reason:
        Human-readable description of the failure.
    """

    def __init__(self, backend: str, reason: str) -> None:
        self.backend = backend
        self.reason = reason
        super().__init__(f"Cache backend '{backend}' error: {reason}")


# ---------------------------------------------------------------------------
# CacheEntry
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    """Single cached item.

    Attributes
    ----------
    key_hash:
        SHA-256 hex digest of the normalised prompt.
    embedding:
        Dense float vector representing the prompt semantics.
    value:
        The cached response value (any JSON-serialisable type).
    created_at:
        Unix timestamp (seconds) when this entry was created.
    ttl_seconds:
        How long the entry lives; ``0`` means never expire.
    tags:
        Optional label list for group invalidation via
        :meth:`SemanticCache.invalidate_by_tag`.
    namespace:
        Logical partition key (default ``"default"``).
    """

    key_hash: str
    embedding: list[float]
    value: Any
    created_at: float
    ttl_seconds: float
    tags: list[str] = field(default_factory=list)
    namespace: str = "default"

    def is_expired(self, now: float | None = None) -> bool:
        """Return ``True`` if this entry has exceeded its TTL."""
        if self.ttl_seconds <= 0:
            return False
        t = now if now is not None else time.time()
        return (t - self.created_at) >= self.ttl_seconds


# ---------------------------------------------------------------------------
# Embedder protocol + default hash-based implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class _EmbedderProtocol(Protocol):  # pragma: no cover
    def embed(self, text: str) -> list[float]: ...


class _HashEmbedder:
    """SHA-256 hash → 256-dim binary float vector (dev/offline use).

    Two identical normalised prompts produce similarity 1.0.
    Different prompts produce ~0.5 cosine similarity on average, well below
    the default threshold of 0.92 — so only exact matches are returned.
    """

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.strip().lower().encode()).digest()
        return [float((b >> i) & 1) for b in digest for i in range(8)]


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two dense float vectors.

    Returns 1.0 for identical vectors and 0.0 when either vector is the zero
    vector.
    """
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class _BackendProtocol(Protocol):  # pragma: no cover
    def get(self, key_hash: str, namespace: str) -> CacheEntry | None: ...
    def set(self, entry: CacheEntry) -> None: ...
    def delete(self, key_hash: str, namespace: str) -> None: ...
    def all_entries(self, namespace: str) -> list[CacheEntry]: ...
    def all_entries_with_tag(self, tag: str) -> list[CacheEntry]: ...
    def clear(self, namespace: str | None = None) -> int: ...


# ---------------------------------------------------------------------------
# InMemoryBackend
# ---------------------------------------------------------------------------


class InMemoryBackend:
    """Thread-safe in-process LRU cache backend.

    Parameters
    ----------
    max_size:
        Maximum number of entries before the oldest-accessed entry is evicted.
        ``0`` means unlimited.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self.max_size = max_size
        # (namespace, key_hash) → CacheEntry, ordered by access time
        self._store: collections.OrderedDict[
            tuple[str, str], CacheEntry
        ] = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, key_hash: str, namespace: str) -> CacheEntry | None:
        with self._lock:
            entry = self._store.get((namespace, key_hash))
            if entry is not None:
                self._store.move_to_end((namespace, key_hash))
            return entry

    def set(self, entry: CacheEntry) -> None:
        with self._lock:
            k = (entry.namespace, entry.key_hash)
            self._store[k] = entry
            self._store.move_to_end(k)
            if self.max_size > 0:
                while len(self._store) > self.max_size:
                    self._store.popitem(last=False)

    def delete(self, key_hash: str, namespace: str) -> None:
        with self._lock:
            self._store.pop((namespace, key_hash), None)

    def all_entries(self, namespace: str) -> list[CacheEntry]:
        with self._lock:
            return [v for (ns, _kh), v in self._store.items() if ns == namespace]

    def all_entries_with_tag(self, tag: str) -> list[CacheEntry]:
        with self._lock:
            return [v for v in self._store.values() if tag in v.tags]

    def clear(self, namespace: str | None = None) -> int:
        with self._lock:
            if namespace is None:
                count = len(self._store)
                self._store.clear()
                return count
            keys = [(ns, kh) for (ns, kh) in list(self._store.keys()) if ns == namespace]
            for k in keys:
                del self._store[k]
            return len(keys)


# ---------------------------------------------------------------------------
# SQLiteBackend
# ---------------------------------------------------------------------------


class SQLiteBackend:
    """Persistent cache backend using stdlib ``sqlite3``.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Use ``":memory:"`` for an
        in-process transient store.
    """

    _CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS agentobs_cache (
        key_hash      TEXT NOT NULL,
        namespace     TEXT NOT NULL DEFAULT 'default',
        embedding     TEXT NOT NULL,
        value         TEXT NOT NULL,
        created_at    REAL NOT NULL,
        ttl_seconds   REAL NOT NULL DEFAULT 0,
        tags          TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (key_hash, namespace)
    )
    """

    def __init__(self, db_path: str = "agentobs_cache.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(self._CREATE_TABLE)

    @contextlib.contextmanager  # type: ignore[misc]
    def _connect(self):  # type: ignore[override]
        """Open a DB connection, yield it for use, commit/rollback, then close it."""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, key_hash: str, namespace: str) -> CacheEntry | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT key_hash, namespace, embedding, value, created_at, ttl_seconds, tags "
                "FROM agentobs_cache WHERE key_hash=? AND namespace=?",
                (key_hash, namespace),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_entry(row)

    def set(self, entry: CacheEntry) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agentobs_cache "
                "(key_hash, namespace, embedding, value, created_at, ttl_seconds, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.key_hash,
                    entry.namespace,
                    json.dumps(entry.embedding),
                    json.dumps(entry.value),
                    entry.created_at,
                    entry.ttl_seconds,
                    ",".join(entry.tags),
                ),
            )

    def delete(self, key_hash: str, namespace: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM agentobs_cache WHERE key_hash=? AND namespace=?",
                (key_hash, namespace),
            )

    def all_entries(self, namespace: str) -> list[CacheEntry]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT key_hash, namespace, embedding, value, created_at, ttl_seconds, tags "
                "FROM agentobs_cache WHERE namespace=?",
                (namespace,),
            ).fetchall()
            return [self._row_to_entry(r) for r in rows]

    def all_entries_with_tag(self, tag: str) -> list[CacheEntry]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT key_hash, namespace, embedding, value, created_at, ttl_seconds, tags "
                "FROM agentobs_cache"
            ).fetchall()
            return [
                self._row_to_entry(r)
                for r in rows
                if tag in (r[6] or "").split(",")
            ]

    def clear(self, namespace: str | None = None) -> int:
        with self._lock, self._connect() as conn:
            if namespace is None:
                count = conn.execute("SELECT COUNT(*) FROM agentobs_cache").fetchone()[0]
                conn.execute("DELETE FROM agentobs_cache")
            else:
                count = conn.execute(
                    "SELECT COUNT(*) FROM agentobs_cache WHERE namespace=?", (namespace,)
                ).fetchone()[0]
                conn.execute(
                    "DELETE FROM agentobs_cache WHERE namespace=?", (namespace,)
                )
            return count

    @staticmethod
    def _row_to_entry(row: tuple) -> CacheEntry:
        key_hash, namespace, embedding_json, value_json, created_at, ttl_seconds, tags_str = row
        return CacheEntry(
            key_hash=key_hash,
            embedding=json.loads(embedding_json),
            value=json.loads(value_json),
            created_at=float(created_at),
            ttl_seconds=float(ttl_seconds),
            tags=[t for t in tags_str.split(",") if t],
            namespace=namespace,
        )


# ---------------------------------------------------------------------------
# RedisBackend
# ---------------------------------------------------------------------------


class RedisBackend:
    """Redis-backed cache backend with TTL managed by Redis itself.

    Parameters
    ----------
    host, port, db:
        Redis connection parameters.
    prefix:
        Key prefix for all entries stored by this backend.

    Raises
    ------
    CacheBackendError
        If the ``redis`` package is not installed or the server is unreachable.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        prefix: str = "agentobs:",
    ) -> None:
        try:
            import redis as _redis  # noqa: PLC0415
        except ImportError as exc:
            raise CacheBackendError(
                "redis",
                "redis package is not installed; run: pip install redis",
            ) from exc
        try:
            self._client = _redis.Redis(host=host, port=port, db=db)
            self._client.ping()
        except Exception as exc:
            raise CacheBackendError("redis", f"Cannot connect to Redis: {exc}") from exc
        self.prefix = prefix
        self._lock = threading.Lock()

    def _key(self, key_hash: str, namespace: str) -> str:
        return f"{self.prefix}{namespace}:{key_hash}"

    def _tag_key(self, tag: str) -> str:
        return f"{self.prefix}tag:{tag}"

    def get(self, key_hash: str, namespace: str) -> CacheEntry | None:
        raw = self._client.get(self._key(key_hash, namespace))
        if raw is None:
            return None
        data = json.loads(raw)
        return CacheEntry(
            key_hash=data["key_hash"],
            embedding=data["embedding"],
            value=data["value"],
            created_at=float(data["created_at"]),
            ttl_seconds=float(data["ttl_seconds"]),
            tags=data.get("tags", []),
            namespace=data.get("namespace", namespace),
        )

    def set(self, entry: CacheEntry) -> None:
        raw = json.dumps({
            "key_hash": entry.key_hash,
            "embedding": entry.embedding,
            "value": entry.value,
            "created_at": entry.created_at,
            "ttl_seconds": entry.ttl_seconds,
            "tags": entry.tags,
            "namespace": entry.namespace,
        })
        key = self._key(entry.key_hash, entry.namespace)
        if entry.ttl_seconds > 0:
            self._client.setex(key, int(entry.ttl_seconds), raw)
        else:
            self._client.set(key, raw)
        # Track memberships for tag-based lookups
        for tag in entry.tags:
            self._client.sadd(self._tag_key(tag), key)

    def delete(self, key_hash: str, namespace: str) -> None:
        self._client.delete(self._key(key_hash, namespace))

    def all_entries(self, namespace: str) -> list[CacheEntry]:
        pattern = f"{self.prefix}{namespace}:*"
        keys = self._client.keys(pattern)
        entries = []
        for k in keys:
            raw = self._client.get(k)
            if raw:
                data = json.loads(raw)
                entries.append(CacheEntry(
                    key_hash=data["key_hash"],
                    embedding=data["embedding"],
                    value=data["value"],
                    created_at=float(data["created_at"]),
                    ttl_seconds=float(data["ttl_seconds"]),
                    tags=data.get("tags", []),
                    namespace=data.get("namespace", namespace),
                ))
        return entries

    def all_entries_with_tag(self, tag: str) -> list[CacheEntry]:
        keys = self._client.smembers(self._tag_key(tag))
        entries = []
        for k in keys:
            raw = self._client.get(k)
            if raw:
                data = json.loads(raw)
                entries.append(CacheEntry(
                    key_hash=data["key_hash"],
                    embedding=data["embedding"],
                    value=data["value"],
                    created_at=float(data["created_at"]),
                    ttl_seconds=float(data["ttl_seconds"]),
                    tags=data.get("tags", []),
                    namespace=data.get("namespace", "default"),
                ))
        return entries

    def clear(self, namespace: str | None = None) -> int:
        if namespace is None:
            pattern = f"{self.prefix}*"
        else:
            pattern = f"{self.prefix}{namespace}:*"
        keys = self._client.keys(pattern)
        if keys:
            self._client.delete(*keys)
        return len(keys)


# ---------------------------------------------------------------------------
# SemanticCache
# ---------------------------------------------------------------------------


class SemanticCache:
    """Semantic prompt cache deduplicating near-identical prompts.

    Parameters
    ----------
    backend:
        Backend selector string (``"memory"``, ``"sqlite"``, ``"redis"``) or
        a pre-constructed backend instance implementing the backend protocol.
    similarity_threshold:
        Minimum cosine similarity to count as a cache hit.  Default ``0.92``
        selects only very similar prompts; ``1.0`` requires exact matches.
    ttl_seconds:
        Default TTL for new entries.  ``0`` means never expire.
    namespace:
        Logical partition key used in backend storage.
    embedder:
        Optional external embedder implementing ``embed(text: str) -> list[float]``.
        Defaults to :class:`_HashEmbedder` (exact-match for dev use).
    db_path:
        Path for SQLite backend (ignored for other backends).
    emit_events:
        When ``True`` (default), emit ``llm.cache.*`` events via agentobs stream.
    """

    def __init__(
        self,
        backend: str | Any = "memory",
        similarity_threshold: float = 0.92,
        ttl_seconds: float = 3600.0,
        namespace: str = "default",
        embedder: Any = None,
        db_path: str = "agentobs_cache.db",
        emit_events: bool = True,
        max_size: int = 1000,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.namespace = namespace
        self.emit_events = emit_events
        self._embedder = embedder or _HashEmbedder()

        if isinstance(backend, str):
            if backend == "memory":
                self._backend: Any = InMemoryBackend(max_size=max_size)
            elif backend == "sqlite":
                self._backend = SQLiteBackend(db_path=db_path)
            elif backend == "redis":
                self._backend = RedisBackend()
            else:
                raise ValueError(
                    f"Unknown backend {backend!r}; expected 'memory', 'sqlite', or 'redis'"
                )
        else:
            self._backend = backend

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def get(self, prompt: str) -> Any | None:
        """Look up *prompt* in the cache.

        Returns the cached value on a hit, or ``None`` on a miss.
        Expired entries are treated as misses and lazily removed.
        """
        key_hash = _hash_text(prompt)
        now = time.time()

        # Fast-path: exact key_hash match in the backend
        existing = self._backend.get(key_hash, self.namespace)
        if existing is not None:
            if existing.is_expired(now):
                self._evict(existing, reason="ttl_expired")
                self._emit_miss(key_hash, similarity=None)
                return None
            self._emit_hit(existing, similarity=1.0)
            return existing.value

        # Slow-path: scan for semantic similarity
        query_emb = self._embedder.embed(prompt)
        best_score = 0.0
        best_entry: CacheEntry | None = None

        for entry in self._backend.all_entries(self.namespace):
            if entry.key_hash == key_hash:
                continue  # already checked above
            if entry.is_expired(now):
                self._evict(entry, reason="ttl_expired")
                continue
            score = _cosine_similarity(query_emb, entry.embedding)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is not None and best_score >= self.similarity_threshold:
            self._emit_hit(best_entry, similarity=best_score)
            return best_entry.value

        self._emit_miss(key_hash, similarity=best_score if best_score > 0 else None)
        return None

    def set(self, prompt: str, value: Any, tags: list[str] | None = None) -> None:
        """Store *value* for *prompt* in the cache."""
        key_hash = _hash_text(prompt)
        embedding = self._embedder.embed(prompt)
        entry = CacheEntry(
            key_hash=key_hash,
            embedding=embedding,
            value=value,
            created_at=time.time(),
            ttl_seconds=self.ttl_seconds,
            tags=list(tags or []),
            namespace=self.namespace,
        )
        self._backend.set(entry)
        self._emit_written(entry)

    def invalidate_by_tag(self, tag: str) -> int:
        """Remove all entries tagged with *tag*.  Returns the number evicted."""
        entries = self._backend.all_entries_with_tag(tag)
        count = 0
        for entry in entries:
            self._evict(entry, reason="manual_invalidation")
            count += 1
        return count

    def invalidate_all(self) -> int:
        """Remove all entries in this cache's namespace.  Returns count removed."""
        count = self._backend.clear(self.namespace)
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict(self, entry: CacheEntry, reason: str) -> None:
        self._backend.delete(entry.key_hash, entry.namespace)
        self._emit_evicted(entry, reason=reason)

    def _emit_hit(self, entry: CacheEntry, similarity: float) -> None:
        if not self.emit_events:
            return
        try:
            from agentobs._stream import _build_event, _dispatch  # noqa: PLC0415
            from agentobs.namespaces.cache import CacheHitPayload  # noqa: PLC0415
            from agentobs.types import EventType  # noqa: PLC0415

            ttl_remaining = None
            if entry.ttl_seconds > 0:
                elapsed = time.time() - entry.created_at
                ttl_remaining = max(0, int(entry.ttl_seconds - elapsed))

            payload = CacheHitPayload(
                key_hash=entry.key_hash,
                namespace=entry.namespace,
                similarity_score=min(1.0, max(0.0, similarity)),
                ttl_remaining_seconds=ttl_remaining,
            )
            event = _build_event(EventType.CACHE_HIT, payload.to_dict())
            _dispatch(event)
        except Exception:  # NOSONAR — never let event emission break the cache
            pass

    def _emit_miss(self, key_hash: str, similarity: float | None) -> None:
        if not self.emit_events:
            return
        try:
            from agentobs._stream import _build_event, _dispatch  # noqa: PLC0415
            from agentobs.namespaces.cache import CacheMissPayload  # noqa: PLC0415
            from agentobs.types import EventType  # noqa: PLC0415

            payload = CacheMissPayload(
                key_hash=key_hash,
                namespace=self.namespace,
                best_similarity_score=similarity,
                similarity_threshold=self.similarity_threshold,
            )
            event = _build_event(EventType.CACHE_MISS, payload.to_dict())
            _dispatch(event)
        except Exception:  # NOSONAR
            pass

    def _emit_written(self, entry: CacheEntry) -> None:
        if not self.emit_events:
            return
        try:
            from agentobs._stream import _build_event, _dispatch  # noqa: PLC0415
            from agentobs.namespaces.cache import CacheWrittenPayload  # noqa: PLC0415
            from agentobs.types import EventType  # noqa: PLC0415

            payload = CacheWrittenPayload(
                key_hash=entry.key_hash,
                namespace=entry.namespace,
                ttl_seconds=int(entry.ttl_seconds),
            )
            event = _build_event(EventType.CACHE_WRITTEN, payload.to_dict())
            _dispatch(event)
        except Exception:  # NOSONAR
            pass

    def _emit_evicted(self, entry: CacheEntry, reason: str) -> None:
        if not self.emit_events:
            return
        try:
            from agentobs._stream import _build_event, _dispatch  # noqa: PLC0415
            from agentobs.namespaces.cache import CacheEvictedPayload  # noqa: PLC0415
            from agentobs.types import EventType  # noqa: PLC0415

            elapsed = int(time.time() - entry.created_at)
            payload = CacheEvictedPayload(
                key_hash=entry.key_hash,
                namespace=entry.namespace,
                eviction_reason=reason,
                entry_age_seconds=elapsed,
            )
            event = _build_event(EventType.CACHE_EVICTED, payload.to_dict())
            _dispatch(event)
        except Exception:  # NOSONAR
            pass


# ---------------------------------------------------------------------------
# @cached() decorator
# ---------------------------------------------------------------------------


def cached(
    fn: F | None = None,
    *,
    threshold: float = 0.92,
    ttl: float = 3600.0,
    namespace: str = "default",
    backend: str | Any = "memory",
    tags: list[str] | None = None,
    emit_events: bool = True,
) -> F | Callable[[F], F]:
    """Cache the return value of an LLM call function by its first positional argument.

    The first positional argument (``prompt``) is used as the cache key.

    Parameters
    ----------
    threshold:
        Cosine similarity threshold for a cache hit.
    ttl:
        Entry TTL in seconds.  ``0`` means never expire.
    namespace:
        Logical partition key.
    backend:
        Backend selector (``"memory"`` | ``"sqlite"`` | ``"redis"``) or instance.
    tags:
        Optional tags attached to stored entries.
    emit_events:
        Emit ``llm.cache.*`` events.

    Usage::

        @cached(threshold=0.95, ttl=3600)
        def call_llm(prompt: str) -> str: ...

        # Bare (uses defaults):
        @cached
        def call_llm(prompt: str) -> str: ...
    """
    _cache = SemanticCache(
        backend=backend,
        similarity_threshold=threshold,
        ttl_seconds=ttl,
        namespace=namespace,
        emit_events=emit_events,
    )
    _tags = list(tags or [])

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                prompt = args[0] if args else kwargs.get("prompt", "")
                hit = _cache.get(str(prompt))
                if hit is not None:
                    return hit
                result = await func(*args, **kwargs)
                _cache.set(str(prompt), result, tags=_tags)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            prompt = args[0] if args else kwargs.get("prompt", "")
            hit = _cache.get(str(prompt))
            if hit is not None:
                return hit
            result = func(*args, **kwargs)
            _cache.set(str(prompt), result, tags=_tags)
            return result

        return sync_wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hash_text(text: str) -> str:
    """Return the SHA-256 hex digest of the normalised *text*."""
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()
