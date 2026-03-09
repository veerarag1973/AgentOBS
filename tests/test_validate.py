"""Tests for agentobs.validate — Event envelope JSON Schema validation.

Coverage targets
----------------
* ``validate_event()`` with a valid minimal event — passes silently.
* ``validate_event()`` with a valid fully-populated event — passes silently.
* ``validate_event()`` raises for events with invalid envelope fields.
* ``validate_event()`` raises for events with empty payload.
* ``validate_event()`` raises if called with a non-Event argument.
* ``load_schema()`` returns a dict with the expected $id.
* ``load_schema()`` caches across calls.
* Both jsonschema-present and stdlib-only validation paths are exercised
  by patching the import.
* ``_stdlib_validate()`` validates all optional fields and raises on bad values.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentobs.event import Event, Tags
from agentobs.exceptions import SchemaValidationError
from agentobs.types import EventType
from agentobs.validate import _stdlib_validate, load_schema, validate_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_event(**kwargs) -> Event:
    """Build the simplest valid event."""
    defaults = {
        "event_type": EventType.TRACE_SPAN_COMPLETED,
        "source": "llm-trace@0.3.1",
        "payload": {"span_name": "run", "status": "ok"},
    }
    defaults.update(kwargs)
    return Event(**defaults)


def _full_event() -> Event:
    """Build an event with every optional field populated."""
    return Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="llm-trace@0.3.1",
        payload={"span_name": "run", "status": "ok"},
        trace_id="a" * 32,
        span_id="b" * 16,
        parent_span_id="c" * 16,
        org_id="org-1",
        team_id="team-a",
        actor_id="user:alice",
        session_id="sess-42",
        tags=Tags(env="production", model="gpt-4o"),
    )


# ===========================================================================
# load_schema
# ===========================================================================


class TestLoadSchema:
    def test_returns_dict(self):
        schema = load_schema()
        assert isinstance(schema, dict)

    def test_has_id(self):
        schema = load_schema()
        assert "$id" in schema
        assert "llm-toolkit-schema" in schema["$id"]

    def test_required_fields_in_schema(self):
        schema = load_schema()
        assert "required" in schema
        for field in ("schema_version", "event_id", "event_type", "timestamp", "source", "payload"):
            assert field in schema["required"]

    def test_caches_across_calls(self):
        """Second call must return the same object (no re-read)."""
        a = load_schema()
        b = load_schema()
        assert a is b

    def test_missing_schema_raises_file_not_found(self, tmp_path, monkeypatch):
        """Point schema path at a non-existent file; expect FileNotFoundError."""
        import agentobs.validate as v_module  # noqa: PLC0415

        original_path = v_module._SCHEMA_PATH
        original_cache = v_module._CACHED_SCHEMA
        try:
            v_module._CACHED_SCHEMA = None
            v_module._SCHEMA_PATH = tmp_path / "nonexistent.json"
            with pytest.raises(FileNotFoundError, match="JSON Schema not found"):
                v_module.load_schema()
        finally:
            v_module._SCHEMA_PATH = original_path
            v_module._CACHED_SCHEMA = original_cache


# ===========================================================================
# validate_event — stdlib path (no jsonschema)
# ===========================================================================


class TestValidateEventStdlib:
    """Force the stdlib-only path by making 'import jsonschema' raise ImportError."""

    def _validate(self, event: Event) -> None:
        with patch.dict(sys.modules, {"jsonschema": None, "jsonschema.exceptions": None}):
            validate_event(event)

    def _validate_raises(self, event: Event) -> SchemaValidationError:
        with patch.dict(sys.modules, {"jsonschema": None, "jsonschema.exceptions": None}):  # noqa: SIM117
            with pytest.raises(SchemaValidationError) as exc_info:
                validate_event(event)
        return exc_info.value

    def test_minimal_valid_event_passes(self):
        self._validate(_minimal_event())

    def test_full_valid_event_passes(self):
        self._validate(_full_event())

    def test_non_event_raises_type_error(self):
        with pytest.raises(TypeError, match="Event instance"):
            validate_event({"event_type": "bad"})  # type: ignore  # NOSONAR

    def test_calls_stdlib_validate_directly(self):
        """Also exercise _stdlib_validate directly."""
        doc = _minimal_event().to_dict()
        _stdlib_validate(doc)  # must not raise

    # --- Bad schema_version ---
    def test_bad_schema_version_raises(self):
        doc = _minimal_event().to_dict()
        doc["schema_version"] = "bad version!"
        exc = self._validate_raises_doc(doc)
        assert "schema_version" in exc.field

    # --- Bad event_id ---
    def test_bad_event_id_raises(self):
        doc = _minimal_event().to_dict()
        doc["event_id"] = "not-a-ulid"
        exc = self._validate_raises_doc(doc)
        assert "event_id" in exc.field

    # --- Bad event_type ---
    def test_bad_event_type_raises(self):
        doc = _minimal_event().to_dict()
        doc["event_type"] = "INVALID_TYPE"
        exc = self._validate_raises_doc(doc)
        assert "event_type" in exc.field

    # --- Bad timestamp ---
    def test_bad_timestamp_raises(self):
        doc = _minimal_event().to_dict()
        doc["timestamp"] = "not-a-timestamp"
        exc = self._validate_raises_doc(doc)
        assert "timestamp" in exc.field

    # --- Bad source ---
    def test_bad_source_raises(self):
        doc = _minimal_event().to_dict()
        doc["source"] = "BadSource"
        exc = self._validate_raises_doc(doc)
        assert "source" in exc.field

    # --- Missing required fields ---
    def test_missing_schema_version_raises(self):
        doc = _minimal_event().to_dict()
        del doc["schema_version"]
        exc = self._validate_raises_doc(doc)
        assert "schema_version" in exc.field

    def test_missing_event_id_raises(self):
        doc = _minimal_event().to_dict()
        del doc["event_id"]
        exc = self._validate_raises_doc(doc)
        assert "event_id" in exc.field

    def test_missing_payload_raises(self):
        doc = _minimal_event().to_dict()
        del doc["payload"]
        exc = self._validate_raises_doc(doc)
        assert "payload" in exc.field

    # --- Bad payload type ---
    def test_non_dict_payload_raises(self):
        doc = _minimal_event().to_dict()
        doc["payload"] = "not-a-dict"
        exc = self._validate_raises_doc(doc)
        assert "payload" in exc.field

    def test_empty_payload_raises(self):
        doc = _minimal_event().to_dict()
        doc["payload"] = {}
        exc = self._validate_raises_doc(doc)
        assert "payload" in exc.field

    # --- Bad non-root input ---
    def test_non_dict_root_raises(self):
        with patch.dict(sys.modules, {"jsonschema": None, "jsonschema.exceptions": None}):  # noqa: SIM117
            with pytest.raises(SchemaValidationError, match="<root>"):
                _stdlib_validate("not-a-dict")  # type: ignore  # NOSONAR

    # --- Optional field patterns ---
    def test_bad_trace_id_raises(self):
        doc = _minimal_event().to_dict()
        doc["trace_id"] = "XXXX"
        exc = self._validate_raises_doc(doc)
        assert "trace_id" in exc.field

    def test_bad_span_id_raises(self):
        doc = _minimal_event().to_dict()
        doc["span_id"] = "too-short"
        exc = self._validate_raises_doc(doc)
        assert "span_id" in exc.field

    def test_bad_parent_span_id_raises(self):
        doc = _minimal_event().to_dict()
        doc["parent_span_id"] = "XXXX"
        exc = self._validate_raises_doc(doc)
        assert "parent_span_id" in exc.field

    def test_bad_checksum_raises(self):
        doc = _minimal_event().to_dict()
        doc["checksum"] = "not-hex-64-chars"
        exc = self._validate_raises_doc(doc)
        assert "checksum" in exc.field

    def test_bad_signature_raises(self):
        doc = _minimal_event().to_dict()
        doc["signature"] = "short"
        exc = self._validate_raises_doc(doc)
        assert "signature" in exc.field

    def test_bad_prev_id_raises(self):
        doc = _minimal_event().to_dict()
        doc["prev_id"] = "bad-ulid"
        exc = self._validate_raises_doc(doc)
        assert "prev_id" in exc.field

    def test_valid_trace_id_passes(self):
        doc = _minimal_event().to_dict()
        doc["trace_id"] = "a" * 32
        _stdlib_validate(doc)

    def test_valid_span_id_passes(self):
        doc = _minimal_event().to_dict()
        doc["span_id"] = "b" * 16
        _stdlib_validate(doc)

    def test_valid_checksum_passes(self):
        doc = _minimal_event().to_dict()
        doc["checksum"] = "sha256:" + "a" * 64
        _stdlib_validate(doc)

    # --- tags validation ---
    def test_non_dict_tags_raises(self):
        doc = _minimal_event().to_dict()
        doc["tags"] = ["env", "prod"]
        exc = self._validate_raises_doc(doc)
        assert "tags" in exc.field

    def test_empty_tag_value_raises(self):
        doc = _minimal_event().to_dict()
        doc["tags"] = {"env": ""}
        exc = self._validate_raises_doc(doc)
        assert "tags" in exc.field

    def test_valid_tags_passes(self):
        doc = _minimal_event().to_dict()
        doc["tags"] = {"env": "production"}
        _stdlib_validate(doc)

    # --- string fields type errors ---
    def test_non_string_event_type_raises(self):
        doc = _minimal_event().to_dict()
        doc["event_type"] = 123
        exc = self._validate_raises_doc(doc)
        assert "event_type" in exc.field

    def test_non_string_source_raises(self):
        doc = _minimal_event().to_dict()
        doc["source"] = 99
        exc = self._validate_raises_doc(doc)
        assert "source" in exc.field

    # Helper to call _stdlib_validate directly
    def _validate_raises_doc(self, doc: dict[str, Any]) -> SchemaValidationError:
        with pytest.raises(SchemaValidationError) as exc_info:
            _stdlib_validate(doc)
        return exc_info.value


# ===========================================================================
# validate_event — jsonschema path
# ===========================================================================


class TestValidateEventWithJsonschema:
    """Test the jsonschema-backed path when the library is available."""

    @pytest.fixture(autouse=True)
    def _require_jsonschema(self):
        try:
            import jsonschema  # noqa: F401, PLC0415
        except ImportError:
            pytest.skip("jsonschema not installed")

    def test_minimal_valid_event_passes(self):
        validate_event(_minimal_event())

    def test_full_valid_event_passes(self):
        validate_event(_full_event())

    def test_bad_event_id_raises(self):
        doc = _minimal_event().to_dict()
        doc["event_id"] = "bad-id"
        # Patch to_dict on a mock event to return the bad doc
        mock_event = MagicMock(spec=Event)
        mock_event.to_dict.return_value = doc
        with pytest.raises(SchemaValidationError):
            validate_event(mock_event)

    def test_non_event_raises_type_error(self):
        with pytest.raises(TypeError):
            validate_event("not-an-event")  # type: ignore

    def test_jsonschema_validation_error_converted(self):
        """Ensure jsonschema.ValidationError becomes SchemaValidationError."""

        # Build a doc that fails the pattern check for event_id.
        doc = _minimal_event().to_dict()
        doc["event_id"] = "invalid!!"

        mock_event = MagicMock(spec=Event)
        mock_event.to_dict.return_value = doc

        with pytest.raises(SchemaValidationError):
            validate_event(mock_event)


# ===========================================================================
# validate_event — integration: Event with signing fields passes
# ===========================================================================


class TestValidateSignedEvent:
    """An event that went through sign() adds checksum/signature/prev_id."""

    def test_signed_event_passes_stdlib(self):

        event = _minimal_event()
        doc = event.to_dict()
        # Simulate signing fields manually.
        doc["checksum"] = "sha256:" + "a" * 64
        doc["signature"] = "hmac-sha256:" + "b" * 64
        from agentobs.ulid import generate as _gen  # noqa: PLC0415
        doc["prev_id"] = _gen()

        _stdlib_validate(doc)  # must not raise


# ===========================================================================
# Ensure _stdlib_validate covers every branch of optional-field checks
# ===========================================================================


class TestStdlibValidateBranchCoverage:
    """White-box coverage of branches not reached through validate_event."""

    def test_non_string_trace_id(self):
        doc = _minimal_event().to_dict()
        doc["trace_id"] = 42
        with pytest.raises(SchemaValidationError, match="trace_id"):
            _stdlib_validate(doc)

    def test_non_string_span_id(self):
        doc = _minimal_event().to_dict()
        doc["span_id"] = []
        with pytest.raises(SchemaValidationError, match="span_id"):
            _stdlib_validate(doc)

    def test_non_string_org_id(self):
        doc = _minimal_event().to_dict()
        doc["org_id"] = 123
        with pytest.raises(SchemaValidationError, match="org_id"):
            _stdlib_validate(doc)

    def test_non_string_tags_key(self):
        doc = _minimal_event().to_dict()
        # Tags dict with empty-string key
        doc["tags"] = {"": "val"}
        with pytest.raises(SchemaValidationError, match="tags"):
            _stdlib_validate(doc)

    def test_valid_prev_id_passes(self):
        from agentobs.ulid import generate as _gen  # noqa: PLC0415
        doc = _minimal_event().to_dict()
        doc["prev_id"] = _gen()
        _stdlib_validate(doc)
