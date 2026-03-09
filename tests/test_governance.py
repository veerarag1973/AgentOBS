"""Tests for agentobs.governance (EventGovernancePolicy)."""

from __future__ import annotations

import warnings
from collections.abc import Generator

import pytest

from agentobs import Event, EventType
from agentobs.governance import (
    EventGovernancePolicy,
    GovernanceViolationError,
    GovernanceWarning,
    check_event,
    get_global_policy,
    set_global_policy,
)
from agentobs.ulid import generate as gen_ulid

FIXED_TIMESTAMP = "2026-03-01T12:00:00.000000Z"


@pytest.fixture(autouse=True)
def _reset_global_policy() -> Generator[None, None, None]:
    """Ensure the global policy is cleared before and after every test."""
    set_global_policy(None)
    yield
    set_global_policy(None)


@pytest.fixture()
def trace_event() -> Event:
    return Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="test",
        payload={"span_name": "x"},
        event_id=gen_ulid(),
        timestamp=FIXED_TIMESTAMP,
    )


@pytest.fixture()
def eval_event() -> Event:
    return Event(
        event_type=EventType.EVAL_SCORE,
        source="test",
        payload={"score": 0.9},
        event_id=gen_ulid(),
        timestamp=FIXED_TIMESTAMP,
    )


# ---------------------------------------------------------------------------
# EventGovernancePolicy.check_event — blocked types
# ---------------------------------------------------------------------------


class TestBlockedTypes:
    def test_blocked_type_raises(self, trace_event: Event) -> None:
        et = str(trace_event.event_type)
        policy = EventGovernancePolicy(blocked_types={et})
        with pytest.raises(GovernanceViolationError) as exc_info:
            policy.check_event(trace_event)
        assert exc_info.value.event_type == et

    def test_non_blocked_type_passes(self, trace_event: Event) -> None:
        policy = EventGovernancePolicy(blocked_types={"SOME_OTHER_TYPE"})
        policy.check_event(trace_event)  # should not raise

    def test_add_blocked_type(self, trace_event: Event) -> None:
        policy = EventGovernancePolicy()
        et = str(trace_event.event_type)
        policy.add_blocked_type(et)
        with pytest.raises(GovernanceViolationError):
            policy.check_event(trace_event)

    def test_add_empty_blocked_type_raises(self) -> None:
        policy = EventGovernancePolicy()
        with pytest.raises(ValueError):
            policy.add_blocked_type("")

    def test_blocked_returns_frozenset(self) -> None:
        policy = EventGovernancePolicy(blocked_types={"A", "B"})
        blocked = policy.blocked()
        assert isinstance(blocked, frozenset)
        assert "A" in blocked


# ---------------------------------------------------------------------------
# EventGovernancePolicy.check_event — deprecated types
# ---------------------------------------------------------------------------


class TestDeprecatedTypes:
    def test_deprecated_type_warns(self, trace_event: Event) -> None:
        et = str(trace_event.event_type)
        policy = EventGovernancePolicy(warn_deprecated={et})
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            policy.check_event(trace_event)
        assert len(w) == 1
        assert issubclass(w[0].category, GovernanceWarning)

    def test_deprecated_type_does_not_raise(self, trace_event: Event) -> None:
        et = str(trace_event.event_type)
        policy = EventGovernancePolicy(warn_deprecated={et})
        # pytest filterwarnings=error means GovernanceWarning is raised.
        # We verify it raises a GovernanceWarning (not a GovernanceViolationError).
        with pytest.warns(GovernanceWarning):
            policy.check_event(trace_event)

    def test_add_deprecated_type(self, trace_event: Event) -> None:
        policy = EventGovernancePolicy()
        et = str(trace_event.event_type)
        policy.add_deprecated_type(et)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            policy.check_event(trace_event)
        assert any(issubclass(warning.category, GovernanceWarning) for warning in w)

    def test_add_empty_deprecated_raises(self) -> None:
        policy = EventGovernancePolicy()
        with pytest.raises(ValueError):
            policy.add_deprecated_type("")

    def test_deprecated_returns_frozenset(self) -> None:
        policy = EventGovernancePolicy(warn_deprecated={"X"})
        dep = policy.deprecated()
        assert isinstance(dep, frozenset)
        assert "X" in dep


# ---------------------------------------------------------------------------
# Custom rules
# ---------------------------------------------------------------------------


class TestCustomRules:
    def test_rule_that_returns_reason_blocks(self, trace_event: Event) -> None:
        policy = EventGovernancePolicy()
        policy.add_rule(lambda e: "disallowed" if "test" in e.source else None)
        with pytest.raises(GovernanceViolationError, match="disallowed"):
            policy.check_event(trace_event)

    def test_rule_that_returns_none_allows(self, trace_event: Event) -> None:
        policy = EventGovernancePolicy()
        policy.add_rule(lambda e: None)
        policy.check_event(trace_event)  # should not raise

    def test_non_callable_rule_raises(self) -> None:
        policy = EventGovernancePolicy()
        with pytest.raises(TypeError):
            policy.add_rule("not-callable")  # type: ignore[arg-type]

    def test_multiple_rules_all_checked(self, trace_event: Event) -> None:
        policy = EventGovernancePolicy()
        call_count = [0]

        def counting_rule(e: Event) -> None:
            call_count[0] += 1

        policy.add_rule(counting_rule)
        policy.add_rule(counting_rule)
        policy.check_event(trace_event)
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# Global policy
# ---------------------------------------------------------------------------


class TestGlobalPolicy:
    def test_no_global_policy_check_event_noop(self, trace_event: Event) -> None:
        assert get_global_policy() is None
        check_event(trace_event)  # should not raise

    def test_set_and_get_global_policy(self) -> None:
        policy = EventGovernancePolicy(blocked_types={"X"})
        set_global_policy(policy)
        assert get_global_policy() is policy

    def test_global_policy_blocks_event(self, trace_event: Event) -> None:
        et = str(trace_event.event_type)
        policy = EventGovernancePolicy(blocked_types={et})
        set_global_policy(policy)
        with pytest.raises(GovernanceViolationError):
            check_event(trace_event)

    def test_clear_global_policy_with_none(self, trace_event: Event) -> None:
        policy = EventGovernancePolicy(blocked_types={str(trace_event.event_type)})
        set_global_policy(policy)
        set_global_policy(None)
        check_event(trace_event)  # should not raise after clearing

    def test_set_invalid_policy_raises(self) -> None:
        with pytest.raises(TypeError):
            set_global_policy("not-a-policy")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# GovernanceViolationError
# ---------------------------------------------------------------------------


class TestGovernanceViolationError:
    def test_attributes(self) -> None:
        err = GovernanceViolationError("MY_TYPE", "reason here")
        assert err.event_type == "MY_TYPE"
        assert err.reason == "reason here"
        assert "MY_TYPE" in str(err)
        assert "reason here" in str(err)
