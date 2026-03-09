"""Tests for agentobs.lint — static analysis checks (AO001–AO005)."""

from __future__ import annotations

import pytest

from agentobs.lint import LintError, run_checks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def codes_in(result: list[LintError]) -> list[str]:
    return [e.code for e in result]


def _check(source: str) -> list[LintError]:
    return run_checks(source.strip(), filename="<test>")


# ---------------------------------------------------------------------------
# Clean code produces no errors
# ---------------------------------------------------------------------------


def test_clean_code_no_errors():
    source = """
x = 1
y = x + 2
"""
    assert _check(source) == []


# ---------------------------------------------------------------------------
# AO001 — Event() missing required fields
# ---------------------------------------------------------------------------


def test_ao001_clean_all_fields_present():
    source = """
from agentobs import Event, EventType
e = Event(event_type=EventType.CACHE_HIT, source="my-agent", payload={})
"""
    errors = _check(source)
    assert "AO001" not in codes_in(errors)


def test_ao001_missing_event_type():
    source = """
from agentobs import Event
e = Event(source="my-agent", payload={})
"""
    errors = _check(source)
    assert "AO001" in codes_in(errors)
    ao001 = [e for e in errors if e.code == "AO001"]
    assert any("event_type" in e.message for e in ao001)


def test_ao001_missing_source():
    source = """
from agentobs import Event
e = Event(event_type="llm.cache.hit", payload={})
"""
    errors = _check(source)
    ao001 = [e for e in errors if e.code == "AO001"]
    assert any("source" in e.message for e in ao001)


def test_ao001_missing_payload():
    source = """
from agentobs import Event
e = Event(event_type="llm.cache.hit", source="x")
"""
    errors = _check(source)
    ao001 = [e for e in errors if e.code == "AO001"]
    assert any("payload" in e.message for e in ao001)


def test_ao001_all_three_missing():
    source = """
from agentobs import Event
e = Event()
"""
    errors = _check(source)
    ao001 = [e for e in errors if e.code == "AO001"]
    assert len(ao001) == 3


def test_ao001_not_triggered_for_non_agentobs_event():
    """Event() not imported from agentobs must not trigger AO001."""
    source = """
from mylib import Event
e = Event(name="click")
"""
    errors = _check(source)
    assert "AO001" not in codes_in(errors)


# ---------------------------------------------------------------------------
# AO002 — bare str for Redactable-typed kwargs
# ---------------------------------------------------------------------------


def test_ao002_clean_redactable_used():
    source = """
from agentobs import Redactable, Event, EventType
e = Event(
    event_type=EventType.TRACE_SPAN_STARTED,
    source="svc",
    payload={},
    actor_id=Redactable("user-123"),
)
"""
    errors = _check(source)
    assert "AO002" not in codes_in(errors)


def test_ao002_flagged_bare_string_actor_id():
    source = """
from agentobs import Event
e = Event(event_type="x", source="s", payload={}, actor_id="raw-string")
"""
    errors = _check(source)
    assert "AO002" in codes_in(errors)
    ao002 = [e for e in errors if e.code == "AO002"]
    assert any("actor_id" in e.message for e in ao002)


def test_ao002_flagged_bare_string_session_id():
    source = """
from agentobs import Event
e = Event(event_type="x", source="s", payload={}, session_id="raw-sess")
"""
    errors = _check(source)
    assert "AO002" in codes_in(errors)


def test_ao002_flagged_bare_string_user_id():
    source = """
from agentobs import Event
e = Event(event_type="x", source="s", payload={}, user_id="raw-user")
"""
    errors = _check(source)
    assert "AO002" in codes_in(errors)


def test_ao002_not_flagged_for_variable():
    """A name reference is acceptable — only str literals are flagged."""
    source = """
from agentobs import Event
user = get_user_id()
e = Event(event_type="x", source="s", payload={}, actor_id=user)
"""
    errors = _check(source)
    assert "AO002" not in codes_in(errors)


# ---------------------------------------------------------------------------
# AO003 — unregistered event_type string literal
# ---------------------------------------------------------------------------


def test_ao003_clean_registered_string():
    source = """
from agentobs import Event
e = Event(event_type="llm.cache.hit", source="s", payload={})
"""
    errors = _check(source)
    assert "AO003" not in codes_in(errors)


def test_ao003_clean_enum_member():
    """Using EventType.X (attribute) must not trigger AO003."""
    source = """
from agentobs import Event, EventType
e = Event(event_type=EventType.CACHE_HIT, source="s", payload={})
"""
    errors = _check(source)
    assert "AO003" not in codes_in(errors)


def test_ao003_flagged_unregistered_string():
    source = """
from agentobs import Event
e = Event(event_type="not.a.real.event", source="s", payload={})
"""
    errors = _check(source)
    assert "AO003" in codes_in(errors)
    ao003 = [e for e in errors if e.code == "AO003"]
    assert any("not.a.real.event" in e.message for e in ao003)


def test_ao003_flagged_empty_string():
    source = """
from agentobs import Event
e = Event(event_type="", source="s", payload={})
"""
    errors = _check(source)
    assert "AO003" in codes_in(errors)


# ---------------------------------------------------------------------------
# AO004 — LLM provider call outside trace context
# ---------------------------------------------------------------------------


def test_ao004_clean_inside_span():
    source = """
from agentobs import tracer
def call_model(client, prompt):
    with tracer.span("llm-call"):
        return client.chat.completions.create(model="gpt-4", messages=[])
"""
    errors = _check(source)
    assert "AO004" not in codes_in(errors)


def test_ao004_clean_inside_agent_run():
    source = """
from agentobs import tracer
def run_agent(client):
    with tracer.agent_run("my-agent"):
        return client.chat.completions.create(model="gpt-4", messages=[])
"""
    errors = _check(source)
    assert "AO004" not in codes_in(errors)


def test_ao004_flagged_bare_function():
    source = """
from agentobs import tracer
def call_model(client, prompt):
    return client.chat.completions.create(model="gpt-4", messages=[])
"""
    errors = _check(source)
    assert "AO004" in codes_in(errors)


def test_ao004_not_flagged_at_module_level():
    """Module-level calls are not flagged — only calls inside functions."""
    source = """
result = client.chat.completions.create(model="gpt-4", messages=[])
"""
    errors = _check(source)
    assert "AO004" not in codes_in(errors)


# ---------------------------------------------------------------------------
# AO005 — emit_* outside agent context
# ---------------------------------------------------------------------------


def test_ao005_clean_inside_agent_run():
    source = """
from agentobs import emit_span, tracer
def do_work():
    with tracer.agent_run("agent"):
        emit_span(event_type="llm.trace.span.completed", source="x", payload={})
"""
    errors = _check(source)
    assert "AO005" not in codes_in(errors)


def test_ao005_clean_inside_agent_step():
    source = """
from agentobs import emit_agent_step, tracer
def do_work():
    with tracer.agent_step("step"):
        emit_agent_step(event_type="llm.trace.agent.step", source="x", payload={})
"""
    errors = _check(source)
    assert "AO005" not in codes_in(errors)


def test_ao005_flagged_outside_agent_context():
    source = """
from agentobs import emit_span, tracer
def do_work():
    emit_span(event_type="llm.trace.span.completed", source="x", payload={})
"""
    errors = _check(source)
    assert "AO005" in codes_in(errors)


def test_ao005_not_flagged_at_module_level():
    """Module-level emit_* calls are not flagged."""
    source = """
from agentobs import emit_span
emit_span(event_type="llm.trace.span.completed", source="x", payload={})
"""
    errors = _check(source)
    assert "AO005" not in codes_in(errors)


# ---------------------------------------------------------------------------
# LintError attributes
# ---------------------------------------------------------------------------


def test_lint_error_attributes():
    source = """
from agentobs import Event
e = Event(source="s", payload={})
"""
    errors = _check(source)
    ao001 = [e for e in errors if e.code == "AO001"][0]
    assert ao001.filename == "<test>"
    assert ao001.line >= 1
    assert ao001.col >= 0
    assert "AO001" in ao001.code
    assert "AO001" in str(ao001)


# ---------------------------------------------------------------------------
# Public API imports
# ---------------------------------------------------------------------------


def test_lint_error_importable_from_package():
    from agentobs.lint import LintError as LE  # noqa: F401
    assert LE is LintError


def test_run_checks_importable_from_package():
    from agentobs.lint import run_checks as rc  # noqa: F401
    assert rc is run_checks


def test_syntax_error_returns_ao000():
    source = "def bad_syntax("
    errors = run_checks(source)
    assert len(errors) == 1
    assert errors[0].code == "AO000"
