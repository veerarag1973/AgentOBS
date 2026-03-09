"""Tests for agentobs.cache — event emission (CACHE_HIT/MISS/WRITTEN/EVICTED)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentobs.cache import SemanticCache
from agentobs.types import EventType


def _cache(**kwargs) -> SemanticCache:
    return SemanticCache(backend="memory", emit_events=True, **kwargs)


# ---------------------------------------------------------------------------
# CACHE_WRITTEN
# ---------------------------------------------------------------------------


def test_cache_written_event_emitted_on_set():
    dispatched = []
    with patch("agentobs._stream._dispatch", side_effect=dispatched.append):
        c = _cache()
        c.set("what is 2+2?", "4")

    assert len(dispatched) == 1
    event = dispatched[0]
    assert event.event_type == EventType.CACHE_WRITTEN
    assert event.payload["key_hash"]
    assert event.payload["namespace"] == "default"


# ---------------------------------------------------------------------------
# CACHE_MISS
# ---------------------------------------------------------------------------


def test_cache_miss_event_emitted_on_miss():
    dispatched = []
    with patch("agentobs._stream._dispatch", side_effect=dispatched.append):
        c = _cache()
        c.get("not in cache")

    assert len(dispatched) == 1
    event = dispatched[0]
    assert event.event_type == EventType.CACHE_MISS


def test_cache_miss_event_has_correct_namespace():
    dispatched = []
    with patch("agentobs._stream._dispatch", side_effect=dispatched.append):
        c = SemanticCache(backend="memory", emit_events=True, namespace="myns")
        c.get("missing prompt")

    assert dispatched[0].payload["namespace"] == "myns"


# ---------------------------------------------------------------------------
# CACHE_HIT
# ---------------------------------------------------------------------------


def test_cache_hit_event_emitted_on_hit():
    c = _cache()
    c.set("tell me a joke", "Why did the AI cross the road?")

    dispatched = []
    with patch("agentobs._stream._dispatch", side_effect=dispatched.append):
        c.get("tell me a joke")

    assert len(dispatched) == 1
    event = dispatched[0]
    assert event.event_type == EventType.CACHE_HIT


def test_cache_hit_event_similarity_score_exact():
    c = _cache()
    c.set("hello", "world")

    dispatched = []
    with patch("agentobs._stream._dispatch", side_effect=dispatched.append):
        c.get("hello")

    event = dispatched[0]
    assert event.payload["similarity_score"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CACHE_EVICTED
# ---------------------------------------------------------------------------


def test_cache_evicted_event_emitted_on_invalidate_by_tag():
    c = _cache()
    c.set("taggedprompt", "value", tags=["group:x"])

    dispatched = []
    with patch("agentobs._stream._dispatch", side_effect=dispatched.append):
        c.invalidate_by_tag("group:x")

    assert len(dispatched) == 1
    event = dispatched[0]
    assert event.event_type == EventType.CACHE_EVICTED
    assert event.payload["eviction_reason"] == "manual_invalidation"


# ---------------------------------------------------------------------------
# emit_events=False suppresses all events
# ---------------------------------------------------------------------------


def test_no_events_when_emit_events_false():
    dispatched = []
    with patch("agentobs._stream._dispatch", side_effect=dispatched.append):
        c = SemanticCache(backend="memory", emit_events=False)
        c.set("prompt", "value")
        c.get("prompt")          # hit
        c.get("other prompt")    # miss
        c.invalidate_by_tag("x")

    assert dispatched == []
