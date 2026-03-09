"""Tests for agentobs.event — Event, Tags, and helpers.

100% branch and line coverage target.
"""

from __future__ import annotations

import datetime
import json
import time
from typing import Any

import pytest

from tests.conftest import FIXED_SPAN_ID, FIXED_TIMESTAMP, FIXED_TRACE_ID
from agentobs.event import (
    SCHEMA_VERSION,
    Event,
    Tags,
    _datetime_to_iso,
    _json_default,
    _parse_timestamp,
    _utcnow_iso,
    _validate_event_id,
    _validate_event_type,
    _validate_hex_id,
    _validate_payload,
    _validate_schema_version,
    _validate_source,
    _validate_string_id,
    _validate_timestamp,
    _validate_ulid_field,
)
from agentobs.exceptions import (
    DeserializationError,
    SchemaValidationError,
    SerializationError,
)
from agentobs.types import EventType
from agentobs.ulid import generate as gen_ulid

# ===========================================================================
# Tags
# ===========================================================================


@pytest.mark.unit
class TestTags:
    def test_creation(self) -> None:
        t = Tags(env="production", model="gpt-4o")
        assert t["env"] == "production"
        assert t["model"] == "gpt-4o"

    def test_empty_tags(self) -> None:
        t = Tags()
        assert len(t) == 0

    def test_len(self) -> None:
        assert len(Tags(a="1", b="2")) == 2

    def test_contains(self) -> None:
        t = Tags(x="y")
        assert "x" in t
        assert "z" not in t

    def test_iter(self) -> None:
        t = Tags(b="2", a="1")
        keys = list(t)
        assert keys == ["a", "b"]  # sorted

    def test_repr(self) -> None:
        t = Tags(env="prod")
        assert "env" in repr(t)
        assert "prod" in repr(t)

    def test_to_dict(self) -> None:
        d = Tags(x="1", y="2").to_dict()
        assert d == {"x": "1", "y": "2"}
        # Verify it's a copy
        d["extra"] = "3"
        assert len(Tags(x="1", y="2")) == 2

    def test_equality_with_tags(self) -> None:
        assert Tags(a="1") == Tags(a="1")
        assert Tags(a="1") != Tags(a="2")

    def test_equality_with_dict(self) -> None:
        assert Tags(a="1") == {"a": "1"}

    def test_equality_with_other_type_returns_notimplemented(self) -> None:
        result = Tags().__eq__(42)
        assert result is NotImplemented

    def test_get_existing_key(self) -> None:
        assert Tags(k="v").get("k") == "v"

    def test_get_missing_key_default(self) -> None:
        assert Tags().get("missing") is None

    def test_get_missing_key_custom_default(self) -> None:
        assert Tags().get("missing", "fallback") == "fallback"

    def test_keys_values_items(self) -> None:
        t = Tags(a="1", b="2")
        assert list(t.keys()) == ["a", "b"]
        assert list(t.values()) == ["1", "2"]
        assert list(t.items()) == [("a", "1"), ("b", "2")]

    def test_immutable_setattr_raises(self) -> None:
        t = Tags(a="1")
        with pytest.raises(AttributeError, match="immutable"):
            t.extra = "x"  # type: ignore[assignment]

    def test_invalid_key_type_raises(self) -> None:
        with pytest.raises(SchemaValidationError, match="key must be a non-empty string"):
            Tags(**{"": "value"})

    def test_invalid_value_type_raises(self) -> None:
        with pytest.raises(SchemaValidationError, match="value must be a non-empty"):
            Tags(key="")  # empty value


# ===========================================================================
# Event construction
# ===========================================================================


@pytest.mark.unit
class TestEventConstruction:
    def test_minimal_event(self, minimal_event: Event) -> None:
        assert minimal_event.event_type == "llm.trace.span.completed"
        assert minimal_event.source == "llm-trace@0.3.1"
        assert minimal_event.schema_version == "2.0"

    def test_auto_event_id(self) -> None:
        event = Event(
            event_type=EventType.CACHE_HIT,
            source="llm-cache@0.1.0",
            payload={"hit": True},
            timestamp=FIXED_TIMESTAMP,
        )
        assert len(event.event_id) == 26
        from agentobs.ulid import validate  # noqa: PLC0415
        assert validate(event.event_id)

    def test_auto_timestamp(self) -> None:
        before = datetime.datetime.now(tz=datetime.timezone.utc)
        event = Event(
            event_type=EventType.CACHE_MISS,
            source="llm-cache@0.1.0",
            payload={"miss": True},
        )
        after = datetime.datetime.now(tz=datetime.timezone.utc)
        ts = _parse_timestamp(event.timestamp)
        assert before <= ts <= after

    def test_all_optional_fields(self, full_event: Event) -> None:
        assert full_event.trace_id == FIXED_TRACE_ID
        assert full_event.span_id == FIXED_SPAN_ID
        assert full_event.parent_span_id == FIXED_SPAN_ID
        assert full_event.org_id == "org_01HX"
        assert full_event.team_id == "team_01HX"
        assert full_event.actor_id == "usr_01HX"
        assert full_event.session_id == "sess_01HX"
        assert full_event.tags is not None
        assert full_event.tags["env"] == "production"

    def test_string_event_type(self) -> None:
        event = Event(
            event_type="llm.cache.hit",
            source="llm-cache@0.1.0",
            payload={"hit": True},
            timestamp=FIXED_TIMESTAMP,
        )
        assert event.event_type == "llm.cache.hit"

    def test_schema_version_default(self) -> None:
        event = Event(
            event_type="llm.cache.miss",
            source="llm-cache@0.1.0",
            payload={"miss": True},
            timestamp=FIXED_TIMESTAMP,
        )
        assert event.schema_version == SCHEMA_VERSION

    def test_custom_schema_version(self) -> None:
        event = Event(
            schema_version="1.1.0",
            event_type="llm.cache.miss",
            source="llm-cache@0.1.0",
            payload={"miss": True},
            timestamp=FIXED_TIMESTAMP,
        )
        assert event.schema_version == "1.1.0"


# ===========================================================================
# Event equality & hashing
# ===========================================================================


@pytest.mark.unit
class TestEventEqualityAndHash:
    def test_same_event_id_equal(self, minimal_event_kwargs: dict[str, Any]) -> None:
        e1 = Event(**minimal_event_kwargs)
        e2 = Event(**minimal_event_kwargs)
        assert e1 == e2

    def test_different_event_id_not_equal(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e1 = Event(**minimal_event_kwargs)
        kwargs2 = dict(minimal_event_kwargs, event_id=gen_ulid())
        e2 = Event(**kwargs2)
        assert e1 != e2

    def test_not_equal_other_type(self, minimal_event: Event) -> None:
        # Checking via __eq__ directly: must return NotImplemented for non-Event
        result = Event.__eq__(minimal_event, "string")
        assert result is NotImplemented
        # Via operator: Python falls back to reflect __eq__, ultimately False
        assert minimal_event != "string"  # noqa: SIM201

    def test_hashable(self, minimal_event: Event) -> None:
        s: set[Event] = {minimal_event}
        assert minimal_event in s

    def test_hash_by_id(self, minimal_event_kwargs: dict[str, Any]) -> None:
        e1 = Event(**minimal_event_kwargs)
        e2 = Event(**minimal_event_kwargs)
        assert hash(e1) == hash(e2)

    def test_repr(self, minimal_event: Event) -> None:
        r = repr(minimal_event)
        assert "Event(" in r
        assert minimal_event.event_id in r

    def test_checksum_property_returns_none_by_default(
        self, minimal_event: Event
    ) -> None:
        """checksum property returns None until sign() is called (Phase 3)."""
        assert minimal_event.checksum is None

    def test_signature_property_returns_none_by_default(
        self, minimal_event: Event
    ) -> None:
        """signature property returns None until sign() is called (Phase 3)."""
        assert minimal_event.signature is None

    def test_prev_id_property(
        self, minimal_event_kwargs: dict[str, Any], valid_ulid: str
    ) -> None:
        """prev_id property returns the value passed at construction."""
        e = Event(**dict(minimal_event_kwargs, prev_id=valid_ulid))
        assert e.prev_id == valid_ulid


# ===========================================================================
# Event.validate()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestEventValidation:
    def test_valid_minimal_event(self, minimal_event: Event) -> None:
        minimal_event.validate()  # must not raise

    def test_valid_full_event(self, full_event: Event) -> None:
        full_event.validate()  # must not raise

    # -- schema_version -------------------------------------------------------

    def test_invalid_schema_version_not_string(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, schema_version=123))  # type: ignore[arg-type]
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "schema_version"

    def test_invalid_schema_version_format(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, schema_version="not-semver"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "schema_version"

    # -- event_id -------------------------------------------------------------

    def test_invalid_event_id_not_string(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, event_id=12345))  # type: ignore[arg-type]
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "event_id"

    def test_invalid_event_id_not_ulid(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, event_id="not-a-ulid"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "event_id"

    # -- event_type -----------------------------------------------------------

    def test_invalid_event_type_not_string(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        # EventType is str, but we bypass construction by using object.__setattr__
        e = Event(**minimal_event_kwargs)
        object.__setattr__(e, "_event_type", 42)
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "event_type"

    def test_invalid_event_type_format(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, event_type="bad-type"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "event_type"

    # -- timestamp ------------------------------------------------------------

    def test_invalid_timestamp_not_string(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**minimal_event_kwargs)
        object.__setattr__(e, "_timestamp", 9999)
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "timestamp"

    def test_invalid_timestamp_format(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, timestamp="2026-03-01"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "timestamp"

    def test_invalid_timestamp_bad_date(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, timestamp="2026-13-32T25:99:00.000000Z"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "timestamp"

    # -- source ---------------------------------------------------------------

    def test_invalid_source_not_string(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**minimal_event_kwargs)
        object.__setattr__(e, "_source", 42)
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "source"

    def test_invalid_source_format(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, source="notvalid"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "source"

    # -- payload --------------------------------------------------------------

    def test_invalid_payload_not_dict(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, payload="not a dict"))  # type: ignore[arg-type]
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "payload"

    def test_invalid_payload_empty_dict(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, payload={}))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "payload"

    # -- trace_id / span_id ---------------------------------------------------

    def test_invalid_trace_id(self, minimal_event_kwargs: dict[str, Any]) -> None:
        e = Event(**dict(minimal_event_kwargs, trace_id="tooshort"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "trace_id"

    def test_invalid_span_id(self, minimal_event_kwargs: dict[str, Any]) -> None:
        e = Event(**dict(minimal_event_kwargs, span_id="tooshort"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "span_id"

    def test_invalid_parent_span_id(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, parent_span_id="X" * 16))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "parent_span_id"

    # -- context ids ----------------------------------------------------------

    def test_empty_org_id_raises(self, minimal_event_kwargs: dict[str, Any]) -> None:
        e = Event(**dict(minimal_event_kwargs, org_id=""))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "org_id"

    def test_non_string_team_id_raises(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**minimal_event_kwargs)
        object.__setattr__(e, "_team_id", 42)
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "team_id"

    def test_empty_actor_id_raises(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, actor_id=""))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "actor_id"

    def test_empty_session_id_raises(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**dict(minimal_event_kwargs, session_id=""))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "session_id"

    # -- prev_id ---------------------------------------------------------------

    def test_invalid_prev_id(self, minimal_event_kwargs: dict[str, Any]) -> None:
        e = Event(**dict(minimal_event_kwargs, prev_id="not-a-ulid"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        assert exc_info.value.field == "prev_id"

    def test_valid_prev_id(self, minimal_event_kwargs: dict[str, Any]) -> None:
        e = Event(**dict(minimal_event_kwargs, prev_id=gen_ulid()))
        e.validate()  # must not raise


# ===========================================================================
# Event.to_dict()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestEventToDict:
    def test_keys_present(self, minimal_event: Event) -> None:
        d = minimal_event.to_dict()
        assert "schema_version" in d
        assert "event_id" in d
        assert "event_type" in d
        assert "timestamp" in d
        assert "source" in d
        assert "payload" in d

    def test_none_fields_omitted_by_default(self, minimal_event: Event) -> None:
        d = minimal_event.to_dict()
        assert "trace_id" not in d
        assert "org_id" not in d

    def test_none_fields_included_when_flag_false(
        self, minimal_event: Event
    ) -> None:
        d = minimal_event.to_dict(omit_none=False)
        assert "trace_id" in d
        assert d["trace_id"] is None

    def test_tags_serialised_as_dict(self, full_event: Event) -> None:
        d = full_event.to_dict()
        assert isinstance(d["tags"], dict)
        assert d["tags"]["env"] == "production"

    def test_event_type_is_string(self, minimal_event: Event) -> None:
        d = minimal_event.to_dict()
        assert isinstance(d["event_type"], str)


# ===========================================================================
# Event.to_json()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestEventToJson:
    def test_returns_string(self, minimal_event: Event) -> None:
        assert isinstance(minimal_event.to_json(), str)

    def test_deterministic(self, minimal_event: Event) -> None:
        assert minimal_event.to_json() == minimal_event.to_json()

    def test_valid_json(self, minimal_event: Event) -> None:
        parsed = json.loads(minimal_event.to_json())
        assert parsed["event_id"] == minimal_event.event_id

    def test_sorted_keys(self, minimal_event: Event) -> None:
        raw = minimal_event.to_json()
        # Round-trip and verify top-level key order is sorted
        data = json.loads(raw)
        keys = list(data.keys())
        assert keys == sorted(keys)

    def test_compact_no_whitespace(self, minimal_event: Event) -> None:
        raw = minimal_event.to_json()
        assert " " not in raw

    def test_full_event_round_trip(self, full_event: Event) -> None:
        json_str = full_event.to_json()
        data = json.loads(json_str)
        assert data["trace_id"] == FIXED_TRACE_ID

    def test_non_serialisable_payload_raises(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(
            **dict(minimal_event_kwargs, payload={"fn": lambda x: x})  # type: ignore[dict-item]
        )
        with pytest.raises(SerializationError, match="non-serialisable"):
            e.to_json()

    def test_datetime_in_payload_serialised(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        dt = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        e = Event(**dict(minimal_event_kwargs, payload={"created_at": dt}))
        raw = e.to_json()
        assert "2026-03-01" in raw

    def test_event_type_enum_serialised_as_string(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**minimal_event_kwargs)
        raw = e.to_json()
        data = json.loads(raw)
        assert data["event_type"] == "llm.trace.span.completed"


# ===========================================================================
# Event.payload_checksum()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestPayloadChecksum:
    def test_returns_sha256_prefix(self, minimal_event: Event) -> None:
        cs = minimal_event.payload_checksum()
        assert cs.startswith("sha256:")

    def test_deterministic(self, minimal_event: Event) -> None:
        assert minimal_event.payload_checksum() == minimal_event.payload_checksum()

    def test_different_payloads_different_checksum(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e1 = Event(**dict(minimal_event_kwargs, payload={"a": "1"}))
        e2 = Event(**dict(minimal_event_kwargs, payload={"a": "2"}))
        assert e1.payload_checksum() != e2.payload_checksum()

    def test_checksum_length(self, minimal_event: Event) -> None:
        # "sha256:" + 64 hex chars
        assert len(minimal_event.payload_checksum()) == 64 + 7


# ===========================================================================
# Event.from_dict() / from_json()  # noqa: ERA001
# ===========================================================================


@pytest.mark.unit
class TestEventDeserialization:
    def test_from_dict_round_trip(self, minimal_event: Event) -> None:
        d = minimal_event.to_dict()
        restored = Event.from_dict(d)
        assert restored.event_id == minimal_event.event_id
        assert restored.event_type == minimal_event.event_type

    def test_from_json_round_trip(self, minimal_event: Event) -> None:
        json_str = minimal_event.to_json()
        restored = Event.from_json(json_str)
        restored.validate()
        assert restored.event_id == minimal_event.event_id

    def test_from_dict_with_tags(self, full_event: Event) -> None:
        d = full_event.to_dict()
        restored = Event.from_dict(d)
        assert restored.tags is not None
        assert restored.tags["env"] == "production"

    def test_from_dict_missing_required_field(self) -> None:
        with pytest.raises(DeserializationError, match="required field"):
            Event.from_dict({
                "event_id": gen_ulid(),
                "event_type": "llm.cache.hit",
                # missing schema_version, timestamp, source, payload
            })

    def test_from_dict_not_a_dict(self) -> None:
        with pytest.raises(DeserializationError, match="expected a JSON object"):
            Event.from_dict(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_from_dict_payload_not_dict(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        e = Event(**minimal_event_kwargs)
        d = e.to_dict()
        d["payload"] = "not a dict"
        with pytest.raises(DeserializationError, match="must be an object"):
            Event.from_dict(d)

    def test_from_dict_missing_payload(self, event_dict: dict[str, Any]) -> None:
        del event_dict["payload"]
        with pytest.raises(DeserializationError, match="'payload' is missing"):
            Event.from_dict(event_dict)

    def test_from_json_invalid_json(self) -> None:
        with pytest.raises(DeserializationError, match="invalid JSON"):
            Event.from_json("{not valid json}")

    def test_from_dict_source_hint_in_error(self) -> None:
        with pytest.raises(DeserializationError) as exc_info:
            Event.from_dict({}, source_hint="test_file.jsonl")
        assert "test_file.jsonl" in str(exc_info.value)

    def test_from_dict_non_string_field(self, event_dict: dict[str, Any]) -> None:
        event_dict["source"] = 42
        with pytest.raises(DeserializationError, match="must be a string"):
            Event.from_dict(event_dict)

    def test_from_dict_tags_not_a_dict(
        self, event_dict: dict[str, Any]
    ) -> None:
        """tags that is not dict-like raises DeserializationError."""
        event_dict["tags"] = "not-a-dict"
        with pytest.raises(DeserializationError, match="unexpected structure"):
            Event.from_dict(event_dict)


# ===========================================================================
# Internal validation helpers — branch coverage
# ===========================================================================


@pytest.mark.unit
class TestValidationHelpers:
    def test_validate_schema_version_valid(self) -> None:
        _validate_schema_version("1.0")
        _validate_schema_version("2.0")

    def test_validate_schema_version_not_string(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_schema_version(1)  # type: ignore[arg-type]  # NOSONAR

    def test_validate_event_id_not_string(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_event_id(42)  # type: ignore[arg-type]  # NOSONAR

    def test_validate_event_type_not_string(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_event_type(99)  # type: ignore[arg-type]  # NOSONAR

    def test_validate_timestamp_not_string(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_timestamp(12345)  # type: ignore[arg-type]  # NOSONAR

    def test_validate_source_not_string(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_source(None)  # type: ignore[arg-type]  # NOSONAR

    def test_validate_payload_list_raises(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_payload([])

    def test_validate_hex_id_not_string(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_hex_id("trace_id", 42, 32)  # type: ignore[arg-type]  # NOSONAR

    def test_validate_string_id_not_string(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_string_id("org_id", 42)  # type: ignore[arg-type]  # NOSONAR

    def test_validate_string_id_empty(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_string_id("org_id", "")

    def test_validate_ulid_field_not_string(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_ulid_field("prev_id", 42)  # type: ignore[arg-type]  # NOSONAR

    def test_validate_ulid_field_invalid(self) -> None:
        with pytest.raises(SchemaValidationError):
            _validate_ulid_field("prev_id", "bad-ulid")


# ===========================================================================
# Serialisation helpers — branch coverage
# ===========================================================================


@pytest.mark.unit
class TestSerializationHelpers:
    def test_json_default_datetime_utc(self) -> None:
        dt = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        result = _json_default(dt)
        assert "2026-03-01" in result

    def test_json_default_naive_datetime(self) -> None:
        dt = datetime.datetime(2026, 3, 1, 12, 0, 0)  # naive
        result = _json_default(dt)
        assert "2026-03-01" in result

    def test_json_default_event_type(self) -> None:
        result = _json_default(EventType.TRACE_SPAN_COMPLETED)
        assert result == "llm.trace.span.completed"

    def test_json_default_unknown_type_raises(self) -> None:
        with pytest.raises(TypeError, match="not JSON serialisable"):
            _json_default(object())

    def test_datetime_to_iso_non_utc(self) -> None:
        tz_plus5 = datetime.timezone(datetime.timedelta(hours=5))
        dt = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=tz_plus5)
        result = _datetime_to_iso(dt)
        # Should be normalised to UTC: 07:00:00
        assert "07:00:00" in result
        assert result.endswith("Z")

    def test_datetime_to_iso_naive(self) -> None:
        dt = datetime.datetime(2026, 1, 1, 0, 0, 0)
        result = _datetime_to_iso(dt)
        assert result.endswith("Z")

    def test_utcnow_iso_format(self) -> None:
        ts = _utcnow_iso()
        assert ts.endswith("Z")
        assert "T" in ts

    def test_parse_timestamp_with_microseconds(self) -> None:
        dt = _parse_timestamp("2026-03-01T12:00:00.123456Z")
        assert dt.year == 2026
        assert dt.month == 3

    def test_parse_timestamp_without_microseconds(self) -> None:
        dt = _parse_timestamp("2026-03-01T12:00:00Z")
        assert dt.second == 0

    def test_parse_timestamp_non_z_suffix(self) -> None:
        """Timestamps with +00:00 suffix (no trailing Z) also round-trip."""
        dt = _parse_timestamp("2026-03-01T12:00:00.123456+00:00")
        assert dt.year == 2026
        assert dt.month == 3


# ===========================================================================
# Performance
# ===========================================================================


@pytest.mark.perf
class TestEventPerformance:
    def test_create_1000_events_under_1s(self) -> None:
        start = time.perf_counter()
        for i in range(1000):
            Event(
                event_type=EventType.TRACE_SPAN_COMPLETED,
                source="llm-trace@0.3.1",
                payload={"i": i},
                timestamp=FIXED_TIMESTAMP,
            )
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"Creating 1000 events took {elapsed:.2f}s (expected < 1s)"

    def test_serialise_1000_events_under_50ms(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        events = [Event(**minimal_event_kwargs) for _ in range(1000)]
        start = time.perf_counter()
        for e in events:
            e.to_json()
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 50, f"Serialising 1000 events took {elapsed_ms:.1f}ms (expected < 50ms)"


# ===========================================================================
# Security
# ===========================================================================


@pytest.mark.security
class TestEventSecurity:
    def test_pii_not_in_schema_validation_error(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        """PII in payload must never surface in a validation error message."""
        pii_payload = {"user_email": "john.doe@acme.com", "request": "help me"}
        e = Event(**dict(minimal_event_kwargs, payload=pii_payload, source="invalid"))
        with pytest.raises(SchemaValidationError) as exc_info:
            e.validate()
        error_msg = str(exc_info.value)
        assert "john.doe@acme.com" not in error_msg
        assert "help me" not in error_msg

    def test_non_serialisable_error_safe(
        self, minimal_event_kwargs: dict[str, Any]
    ) -> None:
        """SerializationError must carry event_id but not full payload content."""
        e = Event(**dict(minimal_event_kwargs, payload={"fn": lambda x: x}))  # type: ignore[dict-item]
        with pytest.raises(SerializationError) as exc_info:
            e.to_json()
        # Should include event_id for correlation but not the lambda repr
        assert exc_info.value.event_id == e.event_id

    def test_deserialization_error_includes_source_hint(self) -> None:
        with pytest.raises(DeserializationError) as exc_info:
            Event.from_json("{}", source_hint="suspicious.jsonl")
        assert "suspicious.jsonl" in str(exc_info.value)
