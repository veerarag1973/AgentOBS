"""Tests for agentobs.cache — SQLiteBackend persistence and scoping."""

from __future__ import annotations

import time

import pytest

from agentobs.cache import SQLiteBackend, SemanticCache


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test_cache.db")


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------


def test_sqlite_persists_across_instances(db_path):
    """Data written by one SQLiteBackend instance must be readable by another."""
    cache1 = SemanticCache(backend="sqlite", db_path=db_path, emit_events=False)
    cache1.set("persistent prompt", "persistent value")

    # Create a second cache pointing at SAME db file
    cache2 = SemanticCache(backend="sqlite", db_path=db_path, emit_events=False)
    assert cache2.get("persistent prompt") == "persistent value"


def test_sqlite_ttl_expiry_on_reload(db_path):
    """An entry written with a short TTL must appear expired on re-read."""
    cache1 = SemanticCache(backend="sqlite", db_path=db_path, ttl_seconds=0.01, emit_events=False)
    cache1.set("expiring prompt", "expiring value")

    time.sleep(0.05)

    cache2 = SemanticCache(backend="sqlite", db_path=db_path, ttl_seconds=0.01, emit_events=False)
    assert cache2.get("expiring prompt") is None


def test_sqlite_namespace_scoping(db_path):
    """clear(namespace=X) must only remove entries in namespace X."""
    backend = SQLiteBackend(db_path=db_path)
    cache_a = SemanticCache(backend=backend, namespace="alpha", emit_events=False)
    cache_b = SemanticCache(backend=backend, namespace="beta", emit_events=False)
    cache_a.set("q", "alpha-answer")
    cache_b.set("q", "beta-answer")
    cache_a.invalidate_all()
    assert cache_a.get("q") is None
    assert cache_b.get("q") == "beta-answer"


def test_sqlite_multiple_entries(db_path):
    """Verify multiple entries are stored and retrieved correctly."""
    cache = SemanticCache(backend="sqlite", db_path=db_path, emit_events=False)
    for i in range(10):
        cache.set(f"prompt-{i}", f"value-{i}")
    for i in range(10):
        assert cache.get(f"prompt-{i}") == f"value-{i}"


def test_sqlite_overwrite_existing_entry(db_path):
    """Setting the same prompt twice must overwrite the old value."""
    cache = SemanticCache(backend="sqlite", db_path=db_path, emit_events=False)
    cache.set("prompt", "original")
    cache.set("prompt", "updated")
    assert cache.get("prompt") == "updated"


def test_sqlite_backend_direct_clear_all(db_path):
    """SQLiteBackend.clear() with no args removes all rows."""
    backend = SQLiteBackend(db_path=db_path)
    cache_a = SemanticCache(backend=backend, namespace="x", emit_events=False)
    cache_b = SemanticCache(backend=backend, namespace="y", emit_events=False)
    cache_a.set("p1", "v1")
    cache_b.set("p2", "v2")
    count = backend.clear()
    assert count == 2

    # Both caches must be empty
    assert cache_a.get("p1") is None
    assert cache_b.get("p2") is None
