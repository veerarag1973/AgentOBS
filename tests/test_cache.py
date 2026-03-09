"""Tests for agentobs.cache — SemanticCache, InMemoryBackend, @cached decorator."""

from __future__ import annotations

import asyncio
import time

import pytest

from agentobs.cache import (
    CacheBackendError,
    CacheEntry,
    InMemoryBackend,
    SemanticCache,
    _hash_text,
    cached,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh(emit_events: bool = False, **kwargs) -> SemanticCache:
    """Return a brand-new SemanticCache backed by InMemory for isolation."""
    return SemanticCache(backend="memory", emit_events=emit_events, **kwargs)


# ---------------------------------------------------------------------------
# Cache miss
# ---------------------------------------------------------------------------


def test_miss_on_empty_cache():
    cache = _fresh()
    assert cache.get("what is the capital of france?") is None


def test_miss_different_prompt():
    cache = _fresh()
    cache.set("hello world", "response A")
    assert cache.get("completely different question") is None


# ---------------------------------------------------------------------------
# Cache hit (exact match)
# ---------------------------------------------------------------------------


def test_hit_after_set():
    cache = _fresh()
    cache.set("what is 2 + 2?", "4")
    assert cache.get("what is 2 + 2?") == "4"


def test_hit_returns_correct_value():
    cache = _fresh()
    cache.set("prompt", {"answer": "42", "model": "gpt-4"})
    result = cache.get("prompt")
    assert result == {"answer": "42", "model": "gpt-4"}


def test_hit_normalised_case():
    """The same text with different casing must map to the same entry."""
    cache = _fresh()
    cache.set("Hello World", "yes")
    assert cache.get("hello world") == "yes"
    assert cache.get("HELLO WORLD") == "yes"


def test_hit_normalised_whitespace():
    """Leading/trailing whitespace must be stripped before hashing."""
    cache = _fresh()
    cache.set("  hello  ", "result")
    assert cache.get("hello") == "result"


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


def test_ttl_entry_expiry():
    """An entry past its TTL must be treated as a miss and removed."""
    cache = _fresh(ttl_seconds=0.01)
    cache.set("prompt", "value")
    time.sleep(0.05)
    assert cache.get("prompt") is None


def test_ttl_entry_not_yet_expired():
    """An entry within its TTL must still be returned."""
    cache = _fresh(ttl_seconds=60)
    cache.set("prompt", "value")
    assert cache.get("prompt") == "value"


def test_cache_entry_is_expired_direct():
    """CacheEntry.is_expired() respects custom 'now' argument."""
    entry = CacheEntry(
        key_hash="abc",
        embedding=[1.0],
        value="x",
        created_at=1000.0,
        ttl_seconds=10.0,
    )
    assert not entry.is_expired(now=1005.0)
    assert entry.is_expired(now=1011.0)


def test_cache_entry_no_ttl_never_expires():
    entry = CacheEntry(
        key_hash="abc",
        embedding=[],
        value="x",
        created_at=0.0,
        ttl_seconds=0.0,
    )
    assert not entry.is_expired(now=9999999.0)


# ---------------------------------------------------------------------------
# Multiple namespaces
# ---------------------------------------------------------------------------


def test_namespaces_are_isolated():
    backend = InMemoryBackend()
    cache_a = SemanticCache(backend=backend, namespace="ns_a", emit_events=False)
    cache_b = SemanticCache(backend=backend, namespace="ns_b", emit_events=False)
    cache_a.set("prompt", "A-VALUE")
    assert cache_b.get("prompt") is None
    assert cache_a.get("prompt") == "A-VALUE"


# ---------------------------------------------------------------------------
# invalidate_by_tag
# ---------------------------------------------------------------------------


def test_invalidate_by_tag_removes_tagged_entries():
    cache = _fresh()
    cache.set("prompt-1", "v1", tags=["model:gpt4"])
    cache.set("prompt-2", "v2", tags=["model:gpt4"])
    cache.set("prompt-3", "v3", tags=["model:claude"])
    count = cache.invalidate_by_tag("model:gpt4")
    assert count == 2
    assert cache.get("prompt-1") is None
    assert cache.get("prompt-2") is None
    assert cache.get("prompt-3") == "v3"


def test_invalidate_by_tag_returns_zero_when_no_match():
    cache = _fresh()
    cache.set("prompt", "value", tags=["other:tag"])
    count = cache.invalidate_by_tag("no-such-tag")
    assert count == 0
    assert cache.get("prompt") == "value"


# ---------------------------------------------------------------------------
# invalidate_all
# ---------------------------------------------------------------------------


def test_invalidate_all_clears_cache():
    cache = _fresh()
    cache.set("prompt-a", "a")
    cache.set("prompt-b", "b")
    count = cache.invalidate_all()
    assert count == 2
    assert cache.get("prompt-a") is None
    assert cache.get("prompt-b") is None


def test_invalidate_all_returns_zero_on_empty():
    cache = _fresh()
    assert cache.invalidate_all() == 0


# ---------------------------------------------------------------------------
# Unknown backend string
# ---------------------------------------------------------------------------


def test_unknown_backend_string_raises_value_error():
    with pytest.raises(ValueError, match="Unknown backend"):
        SemanticCache(backend="postgresql")


# ---------------------------------------------------------------------------
# InMemoryBackend LRU eviction
# ---------------------------------------------------------------------------


def test_in_memory_backend_lru_eviction():
    """Inserting more than max_size entries must evict the oldest entry."""
    cache = SemanticCache(backend="memory", max_size=3, emit_events=False)
    cache.set("prompt-a", "a")
    cache.set("prompt-b", "b")
    cache.set("prompt-c", "c")
    cache.set("prompt-d", "d")  # pushes LRU (prompt-a) out
    assert cache.get("prompt-a") is None
    assert cache.get("prompt-b") == "b"
    assert cache.get("prompt-c") == "c"
    assert cache.get("prompt-d") == "d"


# ---------------------------------------------------------------------------
# @cached decorator — sync
# ---------------------------------------------------------------------------


def test_cached_decorator_wraps_sync_function():
    call_count = 0

    @cached(emit_events=False)
    def call_llm(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"answer:{prompt}"

    result1 = call_llm("hello")
    result2 = call_llm("hello")

    assert result1 == result2 == "answer:hello"
    assert call_count == 1  # second call served from cache


def test_cached_decorator_different_prompts_are_independent():
    @cached(emit_events=False)
    def call_llm(prompt: str) -> str:
        return f"answer:{prompt}"

    assert call_llm("hello") == "answer:hello"
    assert call_llm("goodbye") == "answer:goodbye"


# ---------------------------------------------------------------------------
# @cached decorator — bare (no parentheses)
# ---------------------------------------------------------------------------


def test_cached_bare_no_parens():
    call_count = 0

    @cached
    def call_llm(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"bare:{prompt}"

    assert call_llm("hi") == "bare:hi"
    call_llm("hi")
    assert call_count == 1


# ---------------------------------------------------------------------------
# @cached decorator — async
# ---------------------------------------------------------------------------


def test_cached_async_function():
    call_count = 0

    @cached(emit_events=False)
    async def async_llm(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"async:{prompt}"

    async def run():
        r1 = await async_llm("async prompt")
        r2 = await async_llm("async prompt")
        return r1, r2, call_count

    r1, r2, count = asyncio.run(run())
    assert r1 == r2 == "async:async prompt"
    assert count == 1


# ---------------------------------------------------------------------------
# _hash_text helper
# ---------------------------------------------------------------------------


def test_hash_text_normalizes():
    assert _hash_text("  Hello  ") == _hash_text("hello")
    assert _hash_text("HELLO") == _hash_text("hello")


def test_hash_text_different_inputs_different_hashes():
    assert _hash_text("foo") != _hash_text("bar")


def test_hash_text_returns_hex_string():
    h = _hash_text("test")
    assert len(h) == 64  # SHA-256 = 64 hex chars
    assert all(c in "0123456789abcdef" for c in h)
