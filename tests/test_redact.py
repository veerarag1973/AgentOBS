"""Tests for agentobs.redact — PII redaction framework.

100% branch coverage target.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.conftest import FIXED_TIMESTAMP
from agentobs import Event, EventType
from agentobs.redact import (
    PII_TYPES,
    PIINotRedactedError,
    Redactable,
    RedactionPolicy,
    RedactionResult,
    Sensitivity,
    _count_redactable,
    _has_redactable,
    _utcnow_iso,
    assert_redacted,
    contains_pii,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PII_EMAIL = "alice@example.com"
_SOURCE = "promptlock@1.0.0"


def _simple_event(payload: dict[str, Any]) -> Event:
    return Event(
        event_type=EventType.PROMPT_RENDERED,
        source=_SOURCE,
        payload=payload,
        timestamp=FIXED_TIMESTAMP,
    )


# ===========================================================================
# Sensitivity enum
# ===========================================================================


@pytest.mark.unit
class TestSensitivity:
    def test_all_five_levels_exist(self) -> None:
        assert {s.value for s in Sensitivity} == {"low", "medium", "high", "pii", "phi"}

    def test_string_values(self) -> None:
        assert Sensitivity.LOW.value == "low"
        assert Sensitivity.MEDIUM.value == "medium"
        assert Sensitivity.HIGH.value == "high"
        assert Sensitivity.PII.value == "pii"
        assert Sensitivity.PHI.value == "phi"

    def test_ordering_lt(self) -> None:
        assert Sensitivity.LOW < Sensitivity.MEDIUM
        assert Sensitivity.MEDIUM < Sensitivity.HIGH
        assert Sensitivity.HIGH < Sensitivity.PII
        assert Sensitivity.PII < Sensitivity.PHI

    def test_ordering_le(self) -> None:
        assert Sensitivity.LOW <= Sensitivity.LOW  # NOSONAR
        assert Sensitivity.LOW <= Sensitivity.PII

    def test_ordering_gt(self) -> None:
        assert Sensitivity.PHI > Sensitivity.PII
        assert Sensitivity.PII > Sensitivity.LOW

    def test_ordering_ge(self) -> None:
        assert Sensitivity.PHI >= Sensitivity.PHI  # NOSONAR
        assert Sensitivity.PHI >= Sensitivity.LOW

    def test_lt_non_sensitivity_returns_not_implemented(self) -> None:
        result = Sensitivity.__lt__(Sensitivity.PII, 42)
        assert result is NotImplemented

    def test_le_non_sensitivity_returns_not_implemented(self) -> None:
        result = Sensitivity.__le__(Sensitivity.PII, 42)
        assert result is NotImplemented

    def test_gt_non_sensitivity_returns_not_implemented(self) -> None:
        result = Sensitivity.__gt__(Sensitivity.PII, 42)
        assert result is NotImplemented

    def test_ge_non_sensitivity_returns_not_implemented(self) -> None:
        result = Sensitivity.__ge__(Sensitivity.PII, 42)
        assert result is NotImplemented

    def test_eq_with_string(self) -> None:
        assert Sensitivity.PII == "pii"
        assert Sensitivity.PII == "pii"

    def test_eq_with_same_sensitivity(self) -> None:
        assert Sensitivity.PII == Sensitivity.PII  # NOSONAR

    def test_eq_with_different_sensitivity(self) -> None:
        assert Sensitivity.PII != Sensitivity.PHI

    def test_hash_consistent(self) -> None:
        assert hash(Sensitivity.PII) == hash("pii")
        s1 = {Sensitivity.PII, Sensitivity.PHI}
        assert len(s1) == 2

    def test_is_str_subclass(self) -> None:
        assert isinstance(Sensitivity.PII, str)

    def test_pii_types_constant_is_frozenset(self) -> None:
        assert isinstance(PII_TYPES, frozenset)
        assert "email" in PII_TYPES
        assert "ssn" in PII_TYPES
        assert "phone" in PII_TYPES


# ===========================================================================
# Redactable
# ===========================================================================


@pytest.mark.unit
class TestRedactable:
    def test_construction_with_all_args(self) -> None:
        r = Redactable(_PII_EMAIL, Sensitivity.PII, {"email"})
        assert r.sensitivity == Sensitivity.PII
        assert r.pii_types == frozenset({"email"})

    def test_default_pii_types_empty(self) -> None:
        r = Redactable("value", Sensitivity.HIGH)
        assert r.pii_types == frozenset()

    def test_reveal_returns_original_value(self) -> None:
        r = Redactable(_PII_EMAIL, Sensitivity.PII, {"email"})
        assert r.reveal() == _PII_EMAIL

    def test_repr_hides_value(self) -> None:
        r = Redactable(_PII_EMAIL, Sensitivity.PII, {"email"})
        rep = repr(r)
        assert _PII_EMAIL not in rep
        assert "pii" in rep

    def test_str_hides_value(self) -> None:
        r = Redactable(_PII_EMAIL, Sensitivity.PII, {"email"})
        s = str(r)
        assert _PII_EMAIL not in s
        assert "Redactable" in s

    def test_immutable_setattr_raises(self) -> None:
        r = Redactable(_PII_EMAIL, Sensitivity.PII)
        with pytest.raises(AttributeError, match="immutable"):
            r.sensitivity = Sensitivity.PHI  # type: ignore[misc]

    def test_sensitivity_property(self) -> None:
        r = Redactable(42, Sensitivity.PHI)
        assert r.sensitivity is Sensitivity.PHI

    def test_pii_types_is_frozenset(self) -> None:
        r = Redactable("x", Sensitivity.LOW, {"email", "phone"})
        assert isinstance(r.pii_types, frozenset)
        assert "email" in r.pii_types
        assert "phone" in r.pii_types

    def test_any_type_value_allowed(self) -> None:
        r_int = Redactable(12345, Sensitivity.HIGH)
        assert r_int.reveal() == 12345
        r_dict = Redactable({"key": "value"}, Sensitivity.PII)
        assert r_dict.reveal() == {"key": "value"}


# ===========================================================================
# PIINotRedactedError
# ===========================================================================


@pytest.mark.unit
class TestPIINotRedactedError:
    def test_is_agentobs_error(self) -> None:
        from agentobs.exceptions import LLMSchemaError  # noqa: PLC0415
        err = PIINotRedactedError(count=2)
        assert isinstance(err, LLMSchemaError)

    def test_count_attribute(self) -> None:
        err = PIINotRedactedError(count=3)
        assert err.count == 3

    def test_message_contains_count(self) -> None:
        err = PIINotRedactedError(count=5)
        assert "5" in str(err)

    def test_message_with_context(self) -> None:
        err = PIINotRedactedError(count=1, context="export_to_otlp")
        assert "export_to_otlp" in str(err)

    def test_message_without_context(self) -> None:
        err = PIINotRedactedError(count=1)
        # Without a context argument, the message must not contain the quoted-context suffix
        assert " in '" not in str(err)

    def test_message_never_contains_pii_value(self) -> None:
        err = PIINotRedactedError(count=1, context="test")
        assert _PII_EMAIL not in str(err)


# ===========================================================================
# RedactionPolicy — construction and helpers
# ===========================================================================


@pytest.mark.unit
class TestRedactionPolicyDefaults:
    def test_default_min_sensitivity(self) -> None:
        policy = RedactionPolicy()
        assert policy.min_sensitivity == Sensitivity.PII

    def test_default_redacted_by(self) -> None:
        policy = RedactionPolicy()
        assert policy.redacted_by == "policy:default"

    def test_default_template(self) -> None:
        policy = RedactionPolicy()
        assert "{sensitivity}" in policy.replacement_template

    def test_custom_values(self) -> None:
        policy = RedactionPolicy(
            min_sensitivity=Sensitivity.HIGH,
            redacted_by="policy:corp",
            replacement_template="***{sensitivity}***",
        )
        assert policy.min_sensitivity == Sensitivity.HIGH
        assert policy.redacted_by == "policy:corp"

    def test_immutable_frozen_dataclass(self) -> None:
        from dataclasses import FrozenInstanceError  # noqa: PLC0415
        policy = RedactionPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.redacted_by = "changed"  # type: ignore[misc]

    def test_make_marker(self) -> None:
        policy = RedactionPolicy()
        assert policy._make_marker(Sensitivity.PII) == "[REDACTED:pii]"
        assert policy._make_marker(Sensitivity.PHI) == "[REDACTED:phi]"

    def test_should_redact_above_threshold(self) -> None:
        policy = RedactionPolicy(min_sensitivity=Sensitivity.PII)
        assert policy._should_redact(Redactable("x", Sensitivity.PII)) is True
        assert policy._should_redact(Redactable("x", Sensitivity.PHI)) is True

    def test_should_redact_below_threshold(self) -> None:
        policy = RedactionPolicy(min_sensitivity=Sensitivity.PII)
        assert policy._should_redact(Redactable("x", Sensitivity.LOW)) is False
        assert policy._should_redact(Redactable("x", Sensitivity.HIGH)) is False


# ===========================================================================
# RedactionPolicy.apply()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestRedactionPolicyApply:
    def test_flat_pii_field_redacted(self) -> None:
        policy = RedactionPolicy()
        event = _simple_event(
            {"author": Redactable(_PII_EMAIL, Sensitivity.PII, {"email"}), "version": "v1"}
        )
        result = policy.apply(event)
        assert result.event.payload["author"] == "[REDACTED:pii]"
        assert result.event.payload["version"] == "v1"

    def test_redaction_count_correct(self) -> None:
        policy = RedactionPolicy()
        event = _simple_event(
            {
                "a": Redactable("val1", Sensitivity.PII),
                "b": Redactable("val2", Sensitivity.PHI),
                "c": "plain",
            }
        )
        result = policy.apply(event)
        assert result.redaction_count == 2
        assert result.event.payload["__redaction_count"] == 2

    def test_redaction_metadata_added(self) -> None:
        policy = RedactionPolicy(redacted_by="policy:test")
        event = _simple_event({"x": Redactable("v", Sensitivity.PII)})
        result = policy.apply(event)
        payload = result.event.payload
        assert payload["__redacted_by"] == "policy:test"
        assert payload["__redacted_at"].endswith("Z")

    def test_no_redaction_no_metadata(self) -> None:
        policy = RedactionPolicy()
        event = _simple_event({"key": "plain_value"})
        result = policy.apply(event)
        assert result.redaction_count == 0
        assert "__redacted_at" not in result.event.payload
        assert "__redacted_by" not in result.event.payload

    def test_below_threshold_not_redacted(self) -> None:
        policy = RedactionPolicy(min_sensitivity=Sensitivity.PII)
        event = _simple_event({"note": Redactable("low risk", Sensitivity.LOW)})
        result = policy.apply(event)
        assert result.redaction_count == 0
        assert isinstance(result.event.payload["note"], Redactable)

    def test_nested_dict_redacted(self) -> None:
        policy = RedactionPolicy()
        event = _simple_event(
            {"user": {"email": Redactable(_PII_EMAIL, Sensitivity.PII), "role": "admin"}}
        )
        result = policy.apply(event)
        assert result.event.payload["user"]["email"] == "[REDACTED:pii]"
        assert result.event.payload["user"]["role"] == "admin"

    def test_redactable_in_list_redacted(self) -> None:
        policy = RedactionPolicy()
        event = _simple_event(
            {"items": [Redactable("secret", Sensitivity.PII), "public"]}
        )
        result = policy.apply(event)
        assert result.event.payload["items"][0] == "[REDACTED:pii]"
        assert result.event.payload["items"][1] == "public"

    def test_redactable_in_tuple_redacted(self) -> None:
        """Tuples in payload are recursively scanned."""
        policy = RedactionPolicy()
        r = Redactable("secret", Sensitivity.PII)
        # Access _redact_value directly to cover tuple branch
        counter: list[int] = [0]
        result = policy._redact_value((r, "plain"), counter)
        assert result == ("[REDACTED:pii]", "plain")
        assert counter[0] == 1

    def test_scalar_value_unchanged(self) -> None:
        policy = RedactionPolicy()
        counter: list[int] = [0]
        assert policy._redact_value(42, counter) == 42
        assert policy._redact_value("hello", counter) == "hello"
        assert policy._redact_value(None, counter) is None

    def test_phi_redacted_with_default_policy(self) -> None:
        """PHI level is above PII min_sensitivity, so it should be redacted."""
        policy = RedactionPolicy()
        event = _simple_event({"med": Redactable("diagnosis: X", Sensitivity.PHI)})
        result = policy.apply(event)
        assert result.event.payload["med"] == "[REDACTED:phi]"

    def test_event_identity_preserved(self) -> None:
        policy = RedactionPolicy()
        event = _simple_event({"x": Redactable("v", Sensitivity.PII)})
        result = policy.apply(event)
        assert result.event.event_id == event.event_id
        assert result.event.timestamp == event.timestamp
        assert result.event.source == event.source
        assert result.event.event_type == event.event_type

    def test_optional_fields_preserved(self) -> None:
        policy = RedactionPolicy()
        event = Event(
            event_type=EventType.PROMPT_RENDERED,
            source=_SOURCE,
            payload={"x": Redactable("v", Sensitivity.PII)},
            timestamp=FIXED_TIMESTAMP,
            org_id="org_acme",
            actor_id="usr_alice",
            trace_id="a" * 32,
            span_id="b" * 16,
        )
        result = policy.apply(event)
        assert result.event.org_id == "org_acme"
        assert result.event.actor_id == "usr_alice"
        assert result.event.trace_id == "a" * 32
        assert result.event.span_id == "b" * 16

    def test_tags_preserved(self) -> None:
        from agentobs.event import Tags  # noqa: PLC0415
        policy = RedactionPolicy()
        event = Event(
            event_type=EventType.PROMPT_RENDERED,
            source=_SOURCE,
            payload={"x": Redactable("v", Sensitivity.PII)},
            timestamp=FIXED_TIMESTAMP,
            tags=Tags(env="prod"),
        )
        result = policy.apply(event)
        assert result.event.tags is not None
        assert result.event.tags["env"] == "prod"

    def test_redaction_result_attributes(self) -> None:
        policy = RedactionPolicy(redacted_by="policy:check")
        event = _simple_event({"x": Redactable("v", Sensitivity.PII)})
        result = policy.apply(event)
        assert isinstance(result, RedactionResult)
        assert result.redacted_by == "policy:check"
        assert isinstance(result.redacted_at, str)
        assert result.redacted_at.endswith("Z")

    def test_custom_replacement_template(self) -> None:
        policy = RedactionPolicy(replacement_template="***{sensitivity}***")
        event = _simple_event({"x": Redactable("v", Sensitivity.PII)})
        result = policy.apply(event)
        assert result.event.payload["x"] == "***pii***"

    def test_high_min_sensitivity_policy_ignores_pii(self) -> None:
        """Policy with min_sensitivity=HIGH should redact HIGH and above."""
        policy = RedactionPolicy(min_sensitivity=Sensitivity.HIGH)
        event = _simple_event(
            {
                "a": Redactable("low", Sensitivity.LOW),
                "b": Redactable("high", Sensitivity.HIGH),
                "c": Redactable("pii", Sensitivity.PII),
            }
        )
        result = policy.apply(event)
        assert isinstance(result.event.payload["a"], Redactable)  # below threshold
        assert result.event.payload["b"] == "[REDACTED:high]"
        assert result.event.payload["c"] == "[REDACTED:pii]"


# ===========================================================================
# RedactionResult
# ===========================================================================


@pytest.mark.unit
class TestRedactionResult:
    def test_immutable(self) -> None:
        from dataclasses import FrozenInstanceError  # noqa: PLC0415
        policy = RedactionPolicy()
        event = _simple_event({"x": "y"})
        result = policy.apply(event)
        with pytest.raises(FrozenInstanceError):
            result.redaction_count = 99  # type: ignore[misc]

    def test_all_attributes_present(self) -> None:
        policy = RedactionPolicy()
        event = _simple_event({"x": "y"})
        result = policy.apply(event)
        assert hasattr(result, "event")
        assert hasattr(result, "redaction_count")
        assert hasattr(result, "redacted_at")
        assert hasattr(result, "redacted_by")


# ===========================================================================
# contains_pii()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestContainsPii:
    def test_returns_false_for_clean_payload(self) -> None:
        event = _simple_event({"key": "value", "number": 42})
        assert contains_pii(event) is False

    def test_returns_true_for_redactable_in_payload(self) -> None:
        event = _simple_event({"email": Redactable(_PII_EMAIL, Sensitivity.PII)})
        assert contains_pii(event) is True

    def test_returns_true_for_nested_redactable(self) -> None:
        event = _simple_event({"user": {"email": Redactable(_PII_EMAIL, Sensitivity.PII)}})
        assert contains_pii(event) is True

    def test_returns_true_for_redactable_in_list(self) -> None:
        event = _simple_event({"items": [Redactable("secret", Sensitivity.PII), "ok"]})
        assert contains_pii(event) is True

    def test_returns_false_after_policy_apply(self) -> None:
        policy = RedactionPolicy()
        event = _simple_event({"email": Redactable(_PII_EMAIL, Sensitivity.PII)})
        result = policy.apply(event)
        assert contains_pii(result.event) is False

    def test_returns_true_after_below_threshold_apply(self) -> None:
        """A Redactable below min_sensitivity remains in payload → still PII."""
        policy = RedactionPolicy(min_sensitivity=Sensitivity.PII)
        event = _simple_event({"note": Redactable("low risk", Sensitivity.LOW)})
        result = policy.apply(event)
        assert contains_pii(result.event) is True


# ===========================================================================
# assert_redacted()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestAssertRedacted:
    def test_clean_event_does_not_raise(self) -> None:
        event = _simple_event({"key": "plain"})
        assert_redacted(event)  # must NOT raise

    def test_raises_when_pii_present(self) -> None:
        event = _simple_event({"field": Redactable(_PII_EMAIL, Sensitivity.PII)})
        with pytest.raises(PIINotRedactedError) as exc_info:
            assert_redacted(event)
        assert exc_info.value.count == 1

    def test_count_reflects_number_of_redactables(self) -> None:
        event = _simple_event(
            {
                "a": Redactable("x", Sensitivity.PII),
                "b": Redactable("y", Sensitivity.PHI),
            }
        )
        with pytest.raises(PIINotRedactedError) as exc_info:
            assert_redacted(event)
        assert exc_info.value.count == 2

    def test_context_appears_in_error_message(self) -> None:
        event = _simple_event({"x": Redactable("v", Sensitivity.PII)})
        with pytest.raises(PIINotRedactedError) as exc_info:
            assert_redacted(event, context="export_step")
        assert "export_step" in str(exc_info.value)

    def test_error_message_never_contains_pii_value(self) -> None:
        secret_value = "very-secret-ssn-123-45-6789"  # noqa: S105
        event = _simple_event({"ssn": Redactable(secret_value, Sensitivity.PII)})
        with pytest.raises(PIINotRedactedError) as exc_info:
            assert_redacted(event)
        assert secret_value not in str(exc_info.value)


# ===========================================================================
# Internal helpers
# ===========================================================================


@pytest.mark.unit
class TestInternalHelpers:
    def test_has_redactable_true_for_redactable(self) -> None:
        assert _has_redactable(Redactable("x", Sensitivity.PII)) is True

    def test_has_redactable_true_in_dict(self) -> None:
        assert _has_redactable({"k": Redactable("x", Sensitivity.PII)}) is True

    def test_has_redactable_true_in_list(self) -> None:
        assert _has_redactable([1, Redactable("x", Sensitivity.HIGH), "ok"]) is True

    def test_has_redactable_true_in_tuple(self) -> None:
        assert _has_redactable((Redactable("x", Sensitivity.LOW),)) is True

    def test_has_redactable_false_for_plain_scalar(self) -> None:
        assert _has_redactable("string") is False
        assert _has_redactable(42) is False
        assert _has_redactable(None) is False

    def test_has_redactable_false_for_nested_plain(self) -> None:
        assert _has_redactable({"a": {"b": "c"}}) is False

    def test_count_redactable_zero_for_plain(self) -> None:
        assert _count_redactable("x") == 0
        assert _count_redactable({"a": 1}) == 0

    def test_count_redactable_counts_all(self) -> None:
        data = {
            "a": Redactable("v1", Sensitivity.PII),
            "nested": {"b": Redactable("v2", Sensitivity.PHI)},
            "items": [Redactable("v3", Sensitivity.HIGH)],
        }
        assert _count_redactable(data) == 3

    def test_count_redactable_in_tuple(self) -> None:
        data = (Redactable("x", Sensitivity.PII), "plain")
        assert _count_redactable(data) == 1

    def test_utcnow_iso_format(self) -> None:
        ts = _utcnow_iso()
        assert ts.endswith("Z")
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$", ts)


# ===========================================================================
# Security tests
# ===========================================================================


@pytest.mark.security
class TestRedactionSecurity:
    def test_redactable_repr_never_exposes_value(self) -> None:
        secret = "top-secret-ssn-987-65-4321"  # noqa: S105  # NOSONAR — intentional test value, not a real secret
        r = Redactable(secret, Sensitivity.PII, {"ssn"})
        assert secret not in repr(r)
        assert secret not in str(r)

    def test_redactable_repr_shows_sensitivity(self) -> None:
        r = Redactable("secret", Sensitivity.PHI, {"medical_id"})
        assert "phi" in repr(r)

    def test_policy_apply_does_not_leak_value_in_metadata(self) -> None:
        """The __redacted_at / __redacted_by fields must not contain PII."""
        secret = "876-54-3210-ssn-secret"  # noqa: S105  # NOSONAR — intentional test value, not a real secret
        policy = RedactionPolicy(redacted_by="policy:test")
        event = _simple_event({"ssn": Redactable(secret, Sensitivity.PII)})
        result = policy.apply(event)
        payload = result.event.payload
        for key in ("__redacted_at", "__redacted_by", "__redaction_count"):
            assert secret not in str(payload.get(key, ""))

    def test_replacement_marker_is_safe_to_log(self) -> None:
        """Verify the replacement string contains no PII."""
        policy = RedactionPolicy()
        marker = policy._make_marker(Sensitivity.PII)
        assert _PII_EMAIL not in marker
        assert "REDACTED" in marker


# ===========================================================================
# Performance
# ===========================================================================


@pytest.mark.perf
class TestRedactionPerformance:
    def test_apply_1000_events_under_500ms(self) -> None:
        import time  # noqa: PLC0415
        policy = RedactionPolicy()
        events = [
            _simple_event(
                {
                    "email": Redactable(f"user{i}@example.com", Sensitivity.PII, {"email"}),
                    "note": "plain text note",
                }
            )
            for i in range(1000)
        ]
        t0 = time.perf_counter()
        for event in events:
            policy.apply(event)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"1000 apply() calls took {elapsed:.3f}s > 0.5s"
