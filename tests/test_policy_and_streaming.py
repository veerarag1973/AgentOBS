"""Tests for new runtime-policy classes and streaming iterators.

Coverage targets
----------------
* GuardPolicy — fail-open / fail-closed, custom checkers, check_input, check_output
* FencePolicy — validate, retry_sequence (pass on first, pass on retry, exhaust retries,
                non-retryable failure), attempt normalisation, constructor validation
* TemplatePolicy — validate_variables (all present, some missing), validate_output
                   (no validator, validator returns None, validator returns error),
                   constructor validation, properties
* iter_file — happy path, blank lines, skip_errors=True, skip_errors=False
* aiter_file — same cases but async
* DatadogExporter — dd_site validation
* EventGovernancePolicy — strict_unknown=True with registered and unregistered type
"""

from __future__ import annotations

import asyncio

import pytest

from agentobs import Event, EventType
from agentobs.exceptions import DeserializationError
from agentobs.governance import (
    EventGovernancePolicy,
    GovernanceViolationError,
)

try:
    from agentobs.namespaces.fence import (
        FencePolicy,
        FenceValidationFailedPayload,
        RetryTriggeredPayload,
        ValidationPassedPayload,
    )
except ImportError:
    FencePolicy = FenceValidationFailedPayload = RetryTriggeredPayload = ValidationPassedPayload = None  # type: ignore[assignment]  # noqa: E501
try:
    from agentobs.namespaces.guard import (
        GuardBlockedPayload,
        GuardFlaggedPayload,
        GuardPolicy,
    )
except ImportError:
    GuardBlockedPayload = GuardFlaggedPayload = GuardPolicy = None  # type: ignore[assignment]
try:
    from agentobs.namespaces.template import (
        TemplatePolicy,
        TemplateRenderedPayload,
        TemplateValidationFailedPayload,
        VariableMissingPayload,
    )
except ImportError:
    TemplatePolicy = TemplateRenderedPayload = TemplateValidationFailedPayload = VariableMissingPayload = None  # type: ignore[assignment]  # noqa: E501
from typing import TYPE_CHECKING

from agentobs.stream import EventStream, aiter_file, iter_file
from agentobs.ulid import generate as gen_ulid

if TYPE_CHECKING:
    from pathlib import Path

FIXED_TIMESTAMP = "2026-03-01T12:00:00.000000Z"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: str = "llm.trace.span.completed") -> Event:
    return Event(
        event_type=event_type,
        source="test@1.0.0",
        payload={"status": "ok"},
        event_id=gen_ulid(),
        timestamp=FIXED_TIMESTAMP,
    )


def _events_ndjson(events: list[Event]) -> str:
    return "\n".join(e.to_json() for e in events) + "\n"


# ---------------------------------------------------------------------------
# GuardPolicy
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="GuardPolicy removed in v2.0 schema (uses GuardPayload instead)")
class TestGuardPolicy:
    def test_fail_open_no_checkers_allows_input(self) -> None:
        policy = GuardPolicy()
        assert policy.check_input("abc123") is None

    def test_fail_open_no_checkers_allows_output(self) -> None:
        policy = GuardPolicy()
        assert policy.check_output("abc123") is None

    def test_fail_closed_no_checkers_blocks_input(self) -> None:
        policy = GuardPolicy(fail_closed=True)
        result = policy.check_input("abc123")
        assert isinstance(result, GuardBlockedPayload)
        assert result.violation_types == ["no_checker_configured"]
        assert result.input_hash == "abc123"

    def test_fail_closed_no_checkers_flags_output(self) -> None:
        policy = GuardPolicy(fail_closed=True)
        result = policy.check_output("xyz789")
        assert isinstance(result, GuardFlaggedPayload)
        assert result.flag_types == ["no_checker_configured"]
        assert result.output_hash == "xyz789"

    def test_custom_input_checker_blocks(self) -> None:
        def checker(h: str) -> GuardBlockedPayload | None:
            return GuardBlockedPayload(
                policy_id="p1",
                policy_name="blocklist",
                input_hash=h,
                violation_types=["jailbreak"],
            )

        policy = GuardPolicy(input_checker=checker)
        result = policy.check_input("bad-hash")
        assert isinstance(result, GuardBlockedPayload)
        assert result.violation_types == ["jailbreak"]

    def test_custom_input_checker_allows(self) -> None:
        policy = GuardPolicy(input_checker=lambda h: None)
        assert policy.check_input("safe-hash") is None

    def test_custom_output_checker_flags(self) -> None:
        def checker(h: str) -> GuardFlaggedPayload | None:
            return GuardFlaggedPayload(
                policy_id="p2",
                policy_name="toxic",
                output_hash=h,
                flag_types=["toxic_language"],
                severity="low",
            )

        policy = GuardPolicy(output_checker=checker)
        result = policy.check_output("flagged-hash")
        assert isinstance(result, GuardFlaggedPayload)
        assert result.flag_types == ["toxic_language"]

    def test_custom_output_checker_passes(self) -> None:
        policy = GuardPolicy(output_checker=lambda h: None)
        assert policy.check_output("clean-hash") is None

    def test_fail_closed_with_custom_checker_uses_checker(self) -> None:
        """Custom checker takes precedence over fail-closed default."""
        policy = GuardPolicy(
            input_checker=lambda h: None,
            fail_closed=True,
        )
        # Custom checker returns None → allowed, even though fail_closed=True
        assert policy.check_input("anything") is None


# ---------------------------------------------------------------------------
# FencePolicy
# ---------------------------------------------------------------------------


def _pass_validator(output: str) -> ValidationPassedPayload:
    return ValidationPassedPayload(validator_id="json", format_type="json")


def _fail_validator(output: str) -> FenceValidationFailedPayload:
    return FenceValidationFailedPayload(
        validator_id="json",
        format_type="json",
        errors=["invalid JSON"],
        retryable=True,
    )


def _non_retryable_fail(output: str) -> FenceValidationFailedPayload:
    return FenceValidationFailedPayload(
        validator_id="json",
        format_type="json",
        errors=["schema mismatch"],
        retryable=False,
    )


@pytest.mark.skip(reason="FencePolicy removed in v2.0 schema")
class TestFencePolicy:
    def test_validate_pass(self) -> None:
        policy = FencePolicy(validator=_pass_validator)
        result = policy.validate("{}")
        assert isinstance(result, ValidationPassedPayload)
        assert result.attempt == 1

    def test_validate_fail(self) -> None:
        policy = FencePolicy(validator=_fail_validator)
        result = policy.validate("bad", attempt=2)
        assert isinstance(result, FenceValidationFailedPayload)
        assert result.attempt == 2

    def test_validate_normalises_attempt_on_failed(self) -> None:
        """Payload returned by validator has attempt=1; policy should override to attempt=3."""
        policy = FencePolicy(validator=_fail_validator)
        result = policy.validate("x", attempt=3)
        assert isinstance(result, FenceValidationFailedPayload)
        assert result.attempt == 3

    def test_validate_normalises_attempt_on_passed(self) -> None:
        """Payload returned by validator has attempt=1; policy should override to attempt=2."""
        policy = FencePolicy(validator=_pass_validator)
        result = policy.validate("{}", attempt=2)
        assert isinstance(result, ValidationPassedPayload)
        assert result.attempt == 2

    def test_max_retries_property(self) -> None:
        policy = FencePolicy(validator=_pass_validator, max_retries=5)
        assert policy.max_retries == 5

    def test_constructor_rejects_non_callable(self) -> None:
        with pytest.raises(TypeError, match="callable"):
            FencePolicy(validator="not-callable")  # type: ignore[arg-type]

    def test_constructor_rejects_negative_retries(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            FencePolicy(validator=_pass_validator, max_retries=-1)

    def test_retry_sequence_pass_on_first(self) -> None:
        policy = FencePolicy(validator=_pass_validator, max_retries=2)
        result, retries = policy.retry_sequence(lambda i: "{}")
        assert isinstance(result, ValidationPassedPayload)
        assert retries == []

    def test_retry_sequence_pass_on_second_attempt(self) -> None:
        """Fail on attempt 1, pass on attempt 2."""
        calls = [0]

        def validator(output: str):
            calls[0] += 1
            if calls[0] == 1:
                return _fail_validator(output)
            return _pass_validator(output)

        policy = FencePolicy(validator=validator, max_retries=3)
        result, retries = policy.retry_sequence(lambda i: "output")
        assert isinstance(result, ValidationPassedPayload)
        assert len(retries) == 1
        assert isinstance(retries[0], RetryTriggeredPayload)
        assert retries[0].attempt == 2

    def test_retry_sequence_exhausts_retries(self) -> None:
        policy = FencePolicy(validator=_fail_validator, max_retries=2)
        result, retries = policy.retry_sequence(lambda i: "bad")
        assert isinstance(result, FenceValidationFailedPayload)
        # 1 original + 2 retries → 2 retry events triggered
        assert len(retries) == 2

    def test_retry_sequence_non_retryable_stops_immediately(self) -> None:
        policy = FencePolicy(validator=_non_retryable_fail, max_retries=3)
        result, retries = policy.retry_sequence(lambda i: "bad")
        assert isinstance(result, FenceValidationFailedPayload)
        # Non-retryable → no retries triggered
        assert retries == []


# ---------------------------------------------------------------------------
# TemplatePolicy
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TemplatePolicy removed in v2.0 schema")
class TestTemplatePolicy:
    def test_properties(self) -> None:
        policy = TemplatePolicy("tmpl-1", ["a", "b"], template_version="2.0.0")
        assert policy.template_id == "tmpl-1"
        assert policy.required_variables == ["a", "b"]

    def test_validate_variables_all_present(self) -> None:
        policy = TemplatePolicy("t", ["x", "y"])
        assert policy.validate_variables({"x", "y", "z"}) is None

    def test_validate_variables_some_missing(self) -> None:
        policy = TemplatePolicy("t", ["x", "y", "z"])
        result = policy.validate_variables({"x"})
        assert isinstance(result, VariableMissingPayload)
        assert set(result.missing_variables) == {"y", "z"}
        assert result.template_id == "t"

    def test_validate_output_no_validator_succeeds(self) -> None:
        policy = TemplatePolicy("t", ["x"])
        result = policy.validate_output("Hello world", variable_count=1)
        assert isinstance(result, TemplateRenderedPayload)
        assert result.output_length == len("Hello world")
        assert result.variable_count == 1

    def test_validate_output_uses_required_count_when_none(self) -> None:
        policy = TemplatePolicy("t", ["a", "b", "c"])
        result = policy.validate_output("out")
        assert isinstance(result, TemplateRenderedPayload)
        assert result.variable_count == 3  # len(required_variables)

    def test_validate_output_with_passing_validator(self) -> None:
        policy = TemplatePolicy("t", [], output_validator=lambda s: None)
        result = policy.validate_output("nice output")
        assert isinstance(result, TemplateRenderedPayload)

    def test_validate_output_with_failing_validator(self) -> None:
        policy = TemplatePolicy(
            "t",
            [],
            output_validator=lambda s: "output too long" if len(s) > 5 else None,
        )
        result = policy.validate_output("this is a long output")
        assert isinstance(result, TemplateValidationFailedPayload)
        assert result.validation_errors == ["output too long"]
        assert result.validator == "TemplatePolicy"

    def test_validate_output_with_duration(self) -> None:
        policy = TemplatePolicy("t", [])
        result = policy.validate_output("x", render_duration_ms=12.5)
        assert isinstance(result, TemplateRenderedPayload)
        assert result.render_duration_ms == 12.5

    def test_constructor_rejects_empty_template_id(self) -> None:
        with pytest.raises(ValueError, match="template_id"):
            TemplatePolicy("", [])

    def test_constructor_rejects_non_list_variables(self) -> None:
        with pytest.raises(TypeError, match="list"):
            TemplatePolicy("t", "not-a-list")  # type: ignore[arg-type]

    def test_constructor_rejects_non_string_variable(self) -> None:
        with pytest.raises(TypeError, match="string"):
            TemplatePolicy("t", [123])  # type: ignore[list-item]

    def test_constructor_rejects_non_callable_validator(self) -> None:
        with pytest.raises(TypeError, match="callable"):
            TemplatePolicy("t", [], output_validator="not-callable")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# iter_file / aiter_file
# ---------------------------------------------------------------------------


class TestIterFile:
    def test_yields_all_events(self, tmp_path: Path) -> None:
        events = [_make_event() for _ in range(3)]
        ndjson = tmp_path / "events.ndjson"
        ndjson.write_text(_events_ndjson(events), encoding="utf-8")

        collected = list(iter_file(ndjson))
        assert len(collected) == 3
        assert all(isinstance(e, Event) for e in collected)

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        event = _make_event()
        content = "\n" + event.to_json() + "\n\n"
        f = tmp_path / "e.ndjson"
        f.write_text(content, encoding="utf-8")

        assert list(iter_file(f)) == [event]

    def test_raises_on_bad_line_by_default(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.ndjson"
        f.write_text('{"not": "an event"}\n', encoding="utf-8")

        with pytest.raises(DeserializationError):
            list(iter_file(f))

    def test_skip_errors_skips_bad_lines(self, tmp_path: Path) -> None:
        good = _make_event()
        content = '{"bad": true}\n' + good.to_json() + "\n"
        f = tmp_path / "mixed.ndjson"
        f.write_text(content, encoding="utf-8")

        collected = list(iter_file(f, skip_errors=True))
        assert len(collected) == 1
        assert collected[0] == good

    def test_empty_file_yields_nothing(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.ndjson"
        f.write_text("", encoding="utf-8")
        assert list(iter_file(f)) == []

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        event = _make_event()
        f = tmp_path / "s.ndjson"
        f.write_text(event.to_json() + "\n", encoding="utf-8")
        collected = list(iter_file(str(f)))
        assert collected == [event]


class TestAiterFile:
    def test_yields_all_events(self, tmp_path: Path) -> None:
        events = [_make_event() for _ in range(3)]
        f = tmp_path / "events.ndjson"
        f.write_text(_events_ndjson(events), encoding="utf-8")

        async def _run():
            collected = []
            async for e in aiter_file(f):
                collected.append(e)  # noqa: PERF401
            return collected

        result = asyncio.run(_run())
        assert len(result) == 3

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        event = _make_event()
        content = "\n" + event.to_json() + "\n\n"
        f = tmp_path / "e.ndjson"
        f.write_text(content, encoding="utf-8")

        async def _run():
            return [e async for e in aiter_file(f)]

        assert asyncio.run(_run()) == [event]

    def test_raises_on_bad_line_by_default(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.ndjson"
        f.write_text('{"bad": 1}\n', encoding="utf-8")

        async def _run():
            async for _ in aiter_file(f):
                ...
        with pytest.raises(DeserializationError):
            asyncio.run(_run())

    def test_skip_errors_skips_bad_lines(self, tmp_path: Path) -> None:
        good = _make_event()
        content = '{"bad": true}\n' + good.to_json() + "\n"
        f = tmp_path / "mixed.ndjson"
        f.write_text(content, encoding="utf-8")

        async def _run():
            return [e async for e in aiter_file(f, skip_errors=True)]

        assert asyncio.run(_run()) == [good]

    def test_empty_file_yields_nothing(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.ndjson"
        f.write_text("", encoding="utf-8")

        async def _run():
            return [e async for e in aiter_file(f)]

        assert asyncio.run(_run()) == []


# ---------------------------------------------------------------------------
# DatadogExporter — dd_site validation
# ---------------------------------------------------------------------------


class TestDatadogExporterDdSiteValidation:
    def test_valid_dd_site_accepted(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        exp = DatadogExporter(
            service="svc",
            env="prod",
            agent_url="http://localhost:8126",
            dd_site="datadoghq.com",
        )
        assert exp  # construction succeeds

    def test_empty_dd_site_rejected(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        with pytest.raises(ValueError, match="dd_site"):
            DatadogExporter(
                service="svc",
                env="prod",
                agent_url="http://localhost:8126",
                dd_site="",
            )

    def test_dd_site_with_slash_rejected(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        with pytest.raises(ValueError, match="dd_site"):
            DatadogExporter(
                service="svc",
                env="prod",
                agent_url="http://localhost:8126",
                dd_site="https://datadoghq.com",
            )

    def test_dd_site_without_dot_rejected(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        with pytest.raises(ValueError, match="dd_site"):
            DatadogExporter(
                service="svc",
                env="prod",
                agent_url="http://localhost:8126",
                dd_site="localhost",
            )

    def test_dd_site_with_space_rejected(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        with pytest.raises(ValueError, match="dd_site"):
            DatadogExporter(
                service="svc",
                env="prod",
                agent_url="http://localhost:8126",
                dd_site="datadoghq .com",
            )

    def test_eu_dd_site_accepted(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        exp = DatadogExporter(
            service="svc",
            env="prod",
            agent_url="http://localhost:8126",
            dd_site="datadoghq.eu",
        )
        assert exp

    def test_invalid_agent_url_rejected(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        with pytest.raises(ValueError, match="agent_url"):
            DatadogExporter(
                service="svc",
                env="prod",
                agent_url="not-a-url",
            )

    def test_timeout_zero_rejected(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        with pytest.raises(ValueError, match="timeout"):
            DatadogExporter(
                service="svc",
                env="prod",
                agent_url="http://localhost:8126",
                timeout=0.0,
            )

    def test_empty_service_rejected(self) -> None:
        from agentobs.export.datadog import DatadogExporter  # noqa: PLC0415

        with pytest.raises(ValueError, match="service"):
            DatadogExporter(service="", env="prod")


# ---------------------------------------------------------------------------
# GovernancePolicy — strict_unknown
# ---------------------------------------------------------------------------


class TestGovernancePolicyStrictUnknown:
    def test_strict_unknown_blocks_unregistered_type(self) -> None:
        policy = EventGovernancePolicy(strict_unknown=True)
        event = Event(
            event_type="llm.unknown.totally.fake.type.xyz",
            source="test",
            payload={"x": 1},
            event_id=gen_ulid(),
            timestamp=FIXED_TIMESTAMP,
        )
        with pytest.raises(GovernanceViolationError, match="not registered"):
            policy.check_event(event)

    def test_strict_unknown_allows_registered_type(self) -> None:
        policy = EventGovernancePolicy(strict_unknown=True)
        event = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="test",
            payload={"span_name": "x"},
            event_id=gen_ulid(),
            timestamp=FIXED_TIMESTAMP,
        )
        # Registered type → should not raise
        policy.check_event(event)

    def test_strict_unknown_false_allows_unregistered(self) -> None:
        """Default strict_unknown=False allows arbitrary event types."""
        policy = EventGovernancePolicy(strict_unknown=False)
        event = Event(
            event_type="llm.custom.unregistered",
            source="test",
            payload={"x": 1},
            event_id=gen_ulid(),
            timestamp=FIXED_TIMESTAMP,
        )
        policy.check_event(event)  # should not raise


# ---------------------------------------------------------------------------
# EventStream.from_kafka (mocked kafka-python)
# ---------------------------------------------------------------------------


class TestFromKafka:
    """Tests that mock the kafka module to exercise from_kafka without a broker."""

    def _make_message(self, event: Event, offset: int = 0):
        """Create a mock Kafka message wrapping a JSON-serialised Event."""
        from unittest.mock import MagicMock  # noqa: PLC0415
        msg = MagicMock()
        msg.value = event.to_json()
        msg.offset = offset
        return msg

    def _sentinel_message(self, sentinel: str = "STOP"):
        from unittest.mock import MagicMock  # noqa: PLC0415
        msg = MagicMock()
        msg.value = sentinel
        msg.offset = 0
        return msg

    def test_from_kafka_basic(self) -> None:
        """from_kafka collects events from a mock consumer."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415
        events = [_make_event() for _ in range(3)]
        messages = [self._make_message(e, i) for i, e in enumerate(events)]

        mock_consumer_instance = MagicMock()
        mock_consumer_instance.__iter__ = MagicMock(return_value=iter(messages))
        mock_consumer_instance.close = MagicMock()

        mock_kafka_module = MagicMock()
        mock_kafka_module.KafkaConsumer.return_value = mock_consumer_instance

        with patch.dict("sys.modules", {"kafka": mock_kafka_module}):
            stream = EventStream.from_kafka("test-topic", "localhost:9092")

        assert len(stream) == 3
        mock_consumer_instance.close.assert_called_once()

    def test_from_kafka_sentinel_stops_consumption(self) -> None:
        from unittest.mock import MagicMock, patch  # noqa: PLC0415
        event = _make_event()
        messages = [
            self._make_message(event, 0),
            self._sentinel_message("STOP"),
            self._make_message(event, 2),  # should not be consumed
        ]

        mock_consumer_instance = MagicMock()
        mock_consumer_instance.__iter__ = MagicMock(return_value=iter(messages))
        mock_consumer_instance.close = MagicMock()

        mock_kafka_module = MagicMock()
        mock_kafka_module.KafkaConsumer.return_value = mock_consumer_instance

        with patch.dict("sys.modules", {"kafka": mock_kafka_module}):
            stream = EventStream.from_kafka(
                "test-topic", "localhost:9092", sentinel="STOP"
            )

        assert len(stream) == 1

    def test_from_kafka_max_messages(self) -> None:
        from unittest.mock import MagicMock, patch  # noqa: PLC0415
        events = [_make_event() for _ in range(10)]
        messages = [self._make_message(e, i) for i, e in enumerate(events)]

        mock_consumer_instance = MagicMock()
        mock_consumer_instance.__iter__ = MagicMock(return_value=iter(messages))
        mock_consumer_instance.close = MagicMock()

        mock_kafka_module = MagicMock()
        mock_kafka_module.KafkaConsumer.return_value = mock_consumer_instance

        with patch.dict("sys.modules", {"kafka": mock_kafka_module}):
            stream = EventStream.from_kafka(
                "test-topic", "localhost:9092", max_messages=3
            )

        assert len(stream) == 3

    def test_from_kafka_skip_errors(self) -> None:
        from unittest.mock import MagicMock, patch  # noqa: PLC0415
        good_event = _make_event()

        bad_msg = MagicMock()
        bad_msg.value = '{"not": "an event"}'
        bad_msg.offset = 0

        messages = [bad_msg, self._make_message(good_event, 1)]

        mock_consumer_instance = MagicMock()
        mock_consumer_instance.__iter__ = MagicMock(return_value=iter(messages))
        mock_consumer_instance.close = MagicMock()

        mock_kafka_module = MagicMock()
        mock_kafka_module.KafkaConsumer.return_value = mock_consumer_instance

        with patch.dict("sys.modules", {"kafka": mock_kafka_module}):
            stream = EventStream.from_kafka(
                "test-topic", "localhost:9092", skip_errors=True
            )

        assert len(stream) == 1

    def test_from_kafka_raises_on_bad_message_by_default(self) -> None:
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        from agentobs.exceptions import DeserializationError  # noqa: PLC0415

        bad_msg = MagicMock()
        bad_msg.value = '{"not": "an event"}'
        bad_msg.offset = 5

        mock_consumer_instance = MagicMock()
        mock_consumer_instance.__iter__ = MagicMock(return_value=iter([bad_msg]))
        mock_consumer_instance.close = MagicMock()

        mock_kafka_module = MagicMock()
        mock_kafka_module.KafkaConsumer.return_value = mock_consumer_instance

        with patch.dict("sys.modules", {"kafka": mock_kafka_module}):  # noqa: SIM117
            with pytest.raises(DeserializationError):
                EventStream.from_kafka("test-topic", "localhost:9092")

