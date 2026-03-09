"""Core event envelope for agentobs v0.1.

Every event emitted by every tool in the LLM Developer Toolkit must conform to
the :class:`Event` class defined here.  This is the canonical Python
representation of the JSON event envelope specified in the Enterprise Product
Specification §3.1.

Design goals
------------
* **Zero external dependencies** — only :mod:`datetime`, :mod:`json`,
  :mod:`hashlib`, and :mod:`re` from the standard library.
* **``__slots__``** on all hot-path classes for minimal heap allocation.
* **Deterministic serialisation** — the same :class:`Event` always produces
  the same JSON string; critical for HMAC signing.
* **Typed validation** — every validation failure is a
  :class:`~agentobs.exceptions.SchemaValidationError` with the field name,
  received value, and a clear reason; never a bare :exc:`ValueError`.
* **Immutability after creation** — envelope fields are read-only via
  properties; mutation is limited to the ``sign()`` method (Phase 3) which sets
  ``checksum``, ``signature``, and ``prev_id``.

Serialisation contract
----------------------
``Event.to_json()`` produces canonical JSON with:

* Keys sorted alphabetically at every nesting level.
* ``None`` values **omitted** (reduces wire size; missing key == ``null``).
* :class:`datetime.datetime` values formatted as ``"YYYY-MM-DDTHH:MM:SS.ffffffZ"``.
* :class:`~agentobs.types.EventType` values serialised as their string value.
* :class:`Tags` serialised as a JSON object with sorted string keys.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import sys
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from agentobs.exceptions import (
    DeserializationError,
    SchemaValidationError,
    SerializationError,
)
from agentobs.types import _EVENT_TYPE_RE, EventType
from agentobs.ulid import generate as _generate_ulid
from agentobs.ulid import validate as _validate_ulid

if TYPE_CHECKING:
    from collections.abc import ItemsView, KeysView, Mapping, ValuesView

__all__ = ["SCHEMA_VERSION", "Event", "Tags"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: Final[str] = "2.0"

#: Accepted schema versions for backward-compatibility (RFC-0001 §15.5).
_ACCEPTED_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset({"1.0", "2.0"})

_MUST_BE_STRING: Final[str] = "must be a string"

#: ``service-name@semver`` — e.g. ``my-agent@1.2.0`` or ``MyAgent@1.0.0``
#: RFC-0001 §5.1: first char letter, then letters/digits/._- ; ``@`` ; semver
_SOURCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9._\-]*@\d+\.\d+\.\d+(?:[.\-][a-zA-Z0-9.]+)?$"
)
#: ISO-8601 UTC datetime — EXACTLY 6 decimal places (RFC-0001 §6.1)
_TIMESTAMP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)
#: Schema version — accepts major.minor or major.minor.patch (+ optional prerelease)
_SEMVER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d+\.\d+(?:\.\d+)?(?:[.-][a-zA-Z0-9.]+)?$"
)
#: Trace ID — exactly 32 lowercase hex characters
_TRACE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")
#: Span ID — exactly 16 lowercase hex characters
_SPAN_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{16}$")


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class Tags:
    """Immutable key-value tag container for :class:`Event`.

    Tags are arbitrary string key→value pairs that enrich an event with
    contextual metadata (e.g. ``env``, ``model``, ``region``).

    All keys and values must be non-empty strings.  The container is
    immutable after construction to prevent accidental mutation of a live event.

    Example::

        tags = Tags(env="production", model="gpt-4o", region="us-east-1")
        tags["env"]           # "production"
        "model" in tags       # True
        dict(tags)            # {"env": "production", "model": "gpt-4o", ...}
    """

    __slots__ = ("_data",)

    def __init__(self, **kwargs: str) -> None:
        """Create a new :class:`Tags` instance.

        Args:
            **kwargs: Arbitrary string key=value pairs.

        Raises:
            SchemaValidationError: If any key or value is not a non-empty string.
        """
        for key, value in kwargs.items():
            if not isinstance(key, str) or not key:
                raise SchemaValidationError(
                    field=f"tags.{key!r}",
                    received=key,
                    reason="tag key must be a non-empty string",
                )
            if not isinstance(value, str) or not value:
                raise SchemaValidationError(
                    field=f"tags.{key}",
                    received=value,
                    reason="tag value must be a non-empty string",
                )
        # Store as a sorted immutable snapshot.
        object.__setattr__(self, "_data", dict(sorted(kwargs.items())))

    # ------------------------------------------------------------------
    # Read-only mapping interface
    # ------------------------------------------------------------------

    def __getitem__(self, key: str) -> str:
        return self._data[key]  # type: ignore[index]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __iter__(self):  # type: ignore[override]  # noqa: ANN204
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Tags is immutable; create a new instance instead")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Tags):
            return self._data == other._data
        if isinstance(other, dict):
            return self._data == other
        return NotImplemented

    __hash__: None = None  # Tags is unhashable (mutable-equivalent semantics)

    def __repr__(self) -> str:
        kv = ", ".join(f"{k}={v!r}" for k, v in self._data.items())
        return f"Tags({kv})"

    def get(self, key: str, default: str | None = None) -> str | None:
        """Return the value for *key*, or *default* if not present."""
        return self._data.get(key, default)

    def keys(self) -> KeysView[str]:  # type: ignore[override]
        """Return tag keys."""
        return self._data.keys()

    def values(self) -> ValuesView[str]:  # type: ignore[override]
        """Return tag values."""
        return self._data.values()

    def items(self) -> ItemsView[str, str]:  # type: ignore[override]
        """Return (key, value) pairs."""
        return self._data.items()

    def to_dict(self) -> dict[str, str]:
        """Return a plain :class:`dict` copy of the tags."""
        return dict(self._data)


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


class Event:
    """The canonical event envelope for the LLM Developer Toolkit.

    Every tool in the ecosystem creates events that conform to this class.
    The envelope is designed to map cleanly to OTLP spans/log records (Phase 4)
    and to carry optional HMAC signing for audit integrity (Phase 3).

    Quick start
    -----------
    ::

        from agentobs import Event, EventType, Tags

        event = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="llm-trace@0.3.1",
            payload={"span_name": "run_agent", "status": "ok"},
            tags=Tags(env="production", model="gpt-4o"),
        )
        event.validate()
        json_str = event.to_json()

    Required fields
    ---------------
    * ``schema_version`` — automatically set to ``"1.0"``
    * ``event_id``       — auto-generated ULID if not supplied
    * ``event_type``     — namespaced string or :class:`~agentobs.types.EventType`
    * ``timestamp``      — UTC ISO-8601; auto-generated if not supplied
    * ``source``         — ``"tool-name@semver"``
    * ``payload``        — tool-specific data (non-empty dict)

    All other fields are optional.

    Thread safety
    -------------
    :class:`Event` instances are **not** thread-safe for concurrent mutation.
    Create separate instances per thread/task.
    """

    __slots__ = (
        "_actor_id",
        # Integrity (mutated by sign() in Phase 3)
        "_checksum",
        "_event_id",
        "_event_type",
        # Context
        "_org_id",
        "_parent_span_id",
        "_payload",
        "_prev_id",
        "_schema_version",
        "_session_id",
        "_signature",
        "_source",
        "_span_id",
        # Tags
        "_tags",
        "_team_id",
        "_timestamp",
        # Tracing
        "_trace_id",
    )

    def __init__(  # noqa: PLR0913  # NOSONAR — Event has many optional fields by design
        self,
        *,
        event_type: str | EventType,
        source: str,
        payload: dict[str, Any],
        schema_version: str = SCHEMA_VERSION,
        event_id: str | None = None,
        timestamp: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        org_id: str | None = None,
        team_id: str | None = None,
        actor_id: str | None = None,
        session_id: str | None = None,
        tags: Tags | None = None,
        checksum: str | None = None,
        signature: str | None = None,
        prev_id: str | None = None,
    ) -> None:
        """Create a new :class:`Event`.

        Auto-generated fields
        ---------------------
        * ``event_id`` — a new ULID is generated if not provided.
        * ``timestamp`` — current UTC time is used if not provided.

        Args:
            event_type:     Namespaced event type (string or :class:`EventType`).
            source:         Emitting tool in ``"name@semver"`` format.
            payload:        Tool-specific event data (non-empty dict).
            schema_version: Schema version string.  Defaults to current ``"1.0"``.
            event_id:       ULID.  Auto-generated if omitted.
            timestamp:      UTC ISO-8601 string.  Set to ``utcnow()`` if omitted.
            trace_id:       32-hex-char OpenTelemetry trace ID.
            span_id:        16-hex-char OpenTelemetry span ID.
            parent_span_id: 16-hex-char parent span ID.
            org_id:         Organisation identifier.
            team_id:        Team identifier.
            actor_id:       User or service-account identifier.
            session_id:     Session identifier grouping related events.
            tags:           :class:`Tags` instance with string metadata.
            checksum:       SHA-256 payload checksum (set by ``sign()``).
            signature:      HMAC-SHA256 chain signature (set by ``sign()``).
            prev_id:        ULID of previous event in audit chain (set by ``sign()``).

        Raises:
            SchemaValidationError: If any supplied field has an invalid type or
                value.  The exception carries :attr:`~SchemaValidationError.field`
                and :attr:`~SchemaValidationError.reason`.
        """
        # --- Required fields -------------------------------------------
        object.__setattr__(self, "_schema_version", schema_version)
        object.__setattr__(
            self, "_event_id", event_id if event_id is not None else _generate_ulid()
        )
        # .value gives the canonical string for EventType members; str() is
        # unreliable across Python versions for mixed str+Enum types.
        _et_value: str = (
            event_type.value
            if isinstance(event_type, EventType)
            else str(event_type)
        )
        object.__setattr__(self, "_event_type", _et_value)
        object.__setattr__(
            self,
            "_timestamp",
            timestamp if timestamp is not None else _utcnow_iso(),
        )
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_payload", payload)

        # --- Tracing ---------------------------------------------------
        object.__setattr__(self, "_trace_id", trace_id)
        object.__setattr__(self, "_span_id", span_id)
        object.__setattr__(self, "_parent_span_id", parent_span_id)

        # --- Context ---------------------------------------------------
        object.__setattr__(self, "_org_id", org_id)
        object.__setattr__(self, "_team_id", team_id)
        object.__setattr__(self, "_actor_id", actor_id)
        object.__setattr__(self, "_session_id", session_id)

        # --- Tags / Integrity ------------------------------------------
        object.__setattr__(self, "_tags", tags)
        object.__setattr__(self, "_checksum", checksum)
        object.__setattr__(self, "_signature", signature)
        object.__setattr__(self, "_prev_id", prev_id)

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def schema_version(self) -> str:
        """Schema version string (SemVer)."""
        return self._schema_version  # type: ignore[return-value]

    @property
    def event_id(self) -> str:
        """ULID event identifier."""
        return self._event_id  # type: ignore[return-value]

    @property
    def event_type(self) -> str:
        """Namespaced event type string."""
        return self._event_type  # type: ignore[return-value]

    @property
    def timestamp(self) -> str:
        """UTC ISO-8601 timestamp string."""
        return self._timestamp  # type: ignore[return-value]

    @property
    def source(self) -> str:
        """Emitting tool in ``"name@semver"`` format."""
        return self._source  # type: ignore[return-value]

    @property
    def payload(self) -> Mapping[str, Any]:
        """Tool-specific event payload (read-only view — mutating in place is prevented)."""
        return MappingProxyType(self._payload)  # type: ignore[return-value]

    @property
    def trace_id(self) -> str | None:
        """32-hex-char OpenTelemetry trace ID."""
        return self._trace_id  # type: ignore[return-value]

    @property
    def span_id(self) -> str | None:
        """16-hex-char OpenTelemetry span ID."""
        return self._span_id  # type: ignore[return-value]

    @property
    def parent_span_id(self) -> str | None:
        """16-hex-char parent span ID."""
        return self._parent_span_id  # type: ignore[return-value]

    @property
    def org_id(self) -> str | None:
        """Organisation identifier."""
        return self._org_id  # type: ignore[return-value]

    @property
    def team_id(self) -> str | None:
        """Team identifier."""
        return self._team_id  # type: ignore[return-value]

    @property
    def actor_id(self) -> str | None:
        """User or service-account identifier."""
        return self._actor_id  # type: ignore[return-value]

    @property
    def session_id(self) -> str | None:
        """Session identifier grouping related events."""
        return self._session_id  # type: ignore[return-value]

    @property
    def tags(self) -> Tags | None:
        """Metadata tags."""
        return self._tags  # type: ignore[return-value]

    @property
    def checksum(self) -> str | None:
        """SHA-256 payload checksum.  Set by ``sign()``."""
        return self._checksum  # type: ignore[return-value]

    @property
    def signature(self) -> str | None:
        """HMAC-SHA256 chain signature.  Set by ``sign()``."""
        return self._signature  # type: ignore[return-value]

    @property
    def prev_id(self) -> str | None:
        """ULID of the preceding event in the audit chain.  Set by ``sign()``."""
        return self._prev_id  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Equality & representation
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self._event_id == other._event_id

    def __hash__(self) -> int:
        """Hash by event_id (ULID) — enables set/dict membership."""
        return hash(self._event_id)

    def __repr__(self) -> str:
        return (
            f"Event(event_id={self._event_id!r}, "
            f"event_type={self._event_type!r}, "
            f"source={self._source!r})"
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Validate all envelope fields against the schema specification.

        This method performs deep validation of every field.  Call it
        immediately after constructing an event and before signing or
        exporting.

        Raises:
            SchemaValidationError: On the first field that fails validation.
                ``exc.field`` names the failing field;
                ``exc.reason`` explains the constraint.

        Example::

            event.validate()  # raises SchemaValidationError if invalid
        """
        _validate_schema_version(self._schema_version)  # type: ignore[arg-type]
        _validate_event_id(self._event_id)  # type: ignore[arg-type]
        _validate_event_type(self._event_type)  # type: ignore[arg-type]
        _validate_timestamp(self._timestamp)  # type: ignore[arg-type]
        _validate_source(self._source)  # type: ignore[arg-type]
        _validate_payload(self._payload)  # type: ignore[arg-type]

        # Optional tracing fields
        if self._trace_id is not None:
            _validate_hex_id("trace_id", self._trace_id, 32)  # type: ignore[arg-type]
        if self._span_id is not None:
            _validate_hex_id("span_id", self._span_id, 16)  # type: ignore[arg-type]
        if self._parent_span_id is not None:
            _validate_hex_id("parent_span_id", self._parent_span_id, 16)  # type: ignore[arg-type]

        # Optional context fields
        for field_name, value in [
            ("org_id", self._org_id),
            ("team_id", self._team_id),
            ("actor_id", self._actor_id),
            ("session_id", self._session_id),
        ]:
            if value is not None:
                _validate_string_id(field_name, value)  # type: ignore[arg-type]

        # Optional integrity fields
        if self._prev_id is not None:
            _validate_ulid_field("prev_id", self._prev_id)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self, *, omit_none: bool = True) -> dict[str, Any]:
        """Return a plain :class:`dict` representation.

        The dictionary uses the same field names as the JSON wire format.
        Suitable for passing to logging frameworks or other serialisation
        layers.

        Args:
            omit_none: When ``True`` (default), fields with ``None`` values are
                excluded.  Set to ``False`` to include explicit ``null`` values.

        Returns:
            An ordered dict with string keys and JSON-serialisable values.
        """
        raw: dict[str, Any] = {
            "schema_version": self._schema_version,
            "event_id": self._event_id,
            "event_type": self._event_type,
            "timestamp": self._timestamp,
            "source": self._source,
            "payload": self._payload,
            "trace_id": self._trace_id,
            "span_id": self._span_id,
            "parent_span_id": self._parent_span_id,
            "org_id": self._org_id,
            "team_id": self._team_id,
            "actor_id": self._actor_id,
            "session_id": self._session_id,
            "tags": self._tags.to_dict() if self._tags is not None else None,
            "checksum": self._checksum,
            "signature": self._signature,
            "prev_id": self._prev_id,
        }
        if omit_none:
            return {k: v for k, v in raw.items() if v is not None}
        return raw

    def to_json(self) -> str:
        """Serialise to a canonical, deterministic JSON string.

        Properties
        ----------
        * Keys are sorted alphabetically at every nesting level.
        * ``None`` values are omitted (not serialised as ``null``).
        * Uses compact separators — no whitespace.
        * Guaranteed to be byte-for-byte identical for the same event on any
          supported platform and Python version.

        Returns:
            A compact, canonical JSON string.

        Raises:
            SerializationError: If the payload contains a value that cannot
                be serialised to JSON.

        Example::

            json_str = event.to_json()
            assert json_str == event.to_json()  # deterministic
        """
        try:
            return json.dumps(
                self.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                default=_json_default,
                ensure_ascii=False,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise SerializationError(
                event_id=self._event_id,  # type: ignore[arg-type]
                reason=f"payload contains non-serialisable value: {exc}",
            ) from exc

    def payload_checksum(self) -> str:
        """Compute SHA-256 of the canonical JSON of the payload.

        Used internally by ``sign()`` (Phase 3).  Safe to call at any time to
        get the current payload digest.

        Returns:
            A hex-encoded SHA-256 digest prefixed with ``"sha256:"``.
        """
        canonical = json.dumps(
            self._payload,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
            ensure_ascii=False,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    # ------------------------------------------------------------------
    # Deserialisation
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        source_hint: str = "<dict>",
    ) -> Event:
        """Construct an :class:`Event` from a plain dictionary.

        The dictionary shape matches the output of :meth:`to_dict`.

        Args:
            data:        Dictionary with event fields.
            source_hint: Short label for error messages (e.g. a filename).

        Returns:
            A new :class:`Event` instance (not yet validated).

        Raises:
            DeserializationError: If a required field is missing or has an
                unexpected type.

        Example::

            event = Event.from_dict(json.loads(raw_json))
            event.validate()
        """
        _require_dict(data, source_hint)

        try:
            tags_raw = data.get("tags")
            tags: Tags | None = (
                Tags(**dict(tags_raw.items()))
                if tags_raw is not None
                else None
            )

            return cls(
                schema_version=_require_str(data, "schema_version", source_hint),
                event_id=_require_str(data, "event_id", source_hint),
                event_type=_require_str(data, "event_type", source_hint),
                timestamp=_require_str(data, "timestamp", source_hint),
                source=_require_str(data, "source", source_hint),
                payload=_require_dict_field(data, "payload", source_hint),
                trace_id=data.get("trace_id"),
                span_id=data.get("span_id"),
                parent_span_id=data.get("parent_span_id"),
                org_id=data.get("org_id"),
                team_id=data.get("team_id"),
                actor_id=data.get("actor_id"),
                session_id=data.get("session_id"),
                tags=tags,
                checksum=data.get("checksum"),
                signature=data.get("signature"),
                prev_id=data.get("prev_id"),
            )
        except (KeyError, AttributeError) as exc:
            raise DeserializationError(
                reason=f"unexpected structure: {exc}",
                source_hint=source_hint,
            ) from exc

    @classmethod
    def from_json(cls, json_str: str, *, source_hint: str = "<json>") -> Event:
        """Construct an :class:`Event` from a JSON string.

        Args:
            json_str:    A JSON string in the format produced by :meth:`to_json`.
            source_hint: Short label for error messages.

        Returns:
            A new :class:`Event` instance (not yet validated).

        Raises:
            DeserializationError: If *json_str* is not valid JSON or is missing
                required fields.

        Example::

            event = Event.from_json(raw_json_str)
            event.validate()
        """
        try:
            data: dict[str, Any] = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise DeserializationError(
                reason=f"invalid JSON: {exc}",
                source_hint=source_hint,
            ) from exc
        return cls.from_dict(data, source_hint=source_hint)


# ---------------------------------------------------------------------------
# Validation helpers  (module-private)
# ---------------------------------------------------------------------------


def _validate_schema_version(value: str) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError(
            "schema_version", value, _MUST_BE_STRING
        )
    if value not in _ACCEPTED_SCHEMA_VERSIONS:
        raise SchemaValidationError(
            "schema_version",
            value,
            f"must be one of {sorted(_ACCEPTED_SCHEMA_VERSIONS)!r} (RFC-0001 §15.5)",
        )


def _validate_event_id(value: str) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError("event_id", value, _MUST_BE_STRING)
    if not _validate_ulid(value):
        raise SchemaValidationError(
            "event_id",
            value,
            "must be a valid 26-character ULID (Crockford Base32)",
        )


def _validate_event_type(value: str) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError("event_type", value, _MUST_BE_STRING)
    if not _EVENT_TYPE_RE.match(value):
        raise SchemaValidationError(
            "event_type",
            value,
            "must match 'llm.<ns>.<entity>.<action>' or 'x.<company>.<…>'",
        )


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError("timestamp", value, _MUST_BE_STRING)
    if not _TIMESTAMP_PATTERN.match(value):
        raise SchemaValidationError(
            "timestamp",
            value,
            "must be UTC ISO-8601 format: 'YYYY-MM-DDTHH:MM:SS[.ffffff]Z'",
        )
    # Further check that it is a real date/time
    try:
        _parse_timestamp(value)
    except ValueError as exc:
        raise SchemaValidationError(
            "timestamp", value, f"not a valid date/time: {exc}"
        ) from exc


def _validate_source(value: str) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError("source", value, _MUST_BE_STRING)
    if not _SOURCE_PATTERN.match(value):
        raise SchemaValidationError(
            "source",
            value,
            "must match 'tool-name@semver', e.g. 'llm-trace@0.3.1'",
        )


def _validate_payload(value: object) -> None:
    if not isinstance(value, dict):
        raise SchemaValidationError(
            "payload", value, "must be a non-empty dict"
        )
    if not value:
        raise SchemaValidationError(
            "payload", value, "must be a non-empty dict (empty dict is not allowed)"
        )


def _validate_hex_id(field: str, value: str, expected_len: int) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError(field, value, _MUST_BE_STRING)
    pattern = _TRACE_ID_PATTERN if expected_len == 32 else _SPAN_ID_PATTERN  # noqa: PLR2004
    if not pattern.match(value):
        raise SchemaValidationError(
            field,
            value,
            f"must be exactly {expected_len} lowercase hex characters",
        )


def _validate_string_id(field: str, value: str) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError(field, value, _MUST_BE_STRING)
    if not value:
        raise SchemaValidationError(
            field, value, "must be a non-empty string"
        )


def _validate_ulid_field(field: str, value: str) -> None:
    if not isinstance(value, str):
        raise SchemaValidationError(field, value, _MUST_BE_STRING)
    if not _validate_ulid(value):
        raise SchemaValidationError(
            field, value, "must be a valid 26-character ULID"
        )


# ---------------------------------------------------------------------------
# Deserialisation helpers  (module-private)
# ---------------------------------------------------------------------------


def _require_dict(data: object, source_hint: str) -> None:
    if not isinstance(data, dict):
        raise DeserializationError(
            reason=f"expected a JSON object at top level, got {type(data).__name__}",
            source_hint=source_hint,
        )


def _require_str(data: dict[str, Any], key: str, source_hint: str) -> str:
    value = data.get(key)
    if value is None:
        raise DeserializationError(
            reason=f"required field '{key}' is missing",
            source_hint=source_hint,
        )
    if not isinstance(value, str):
        raise DeserializationError(
            reason=f"field '{key}' must be a string, got {type(value).__name__}",
            source_hint=source_hint,
        )
    return value


def _require_dict_field(
    data: dict[str, Any], key: str, source_hint: str
) -> dict[str, Any]:
    value = data.get(key)
    if value is None:
        raise DeserializationError(
            reason=f"required field '{key}' is missing",
            source_hint=source_hint,
        )
    if not isinstance(value, dict):
        raise DeserializationError(
            reason=f"field '{key}' must be an object, got {type(value).__name__}",
            source_hint=source_hint,
        )
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Serialisation helpers  (module-private)
# ---------------------------------------------------------------------------


def _json_default(obj: object) -> object:
    """JSON serialiser fallback for non-standard types."""
    if isinstance(obj, datetime.datetime):
        return _datetime_to_iso(obj)
    if isinstance(obj, EventType):
        return obj.value
    raise TypeError(f"Object of type {type(obj).__name__!r} is not JSON serialisable")


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return _datetime_to_iso(now)


def _datetime_to_iso(dt: datetime.datetime) -> str:
    """Format a :class:`datetime.datetime` as ``'YYYY-MM-DDTHH:MM:SS.ffffffZ'``."""
    if dt.tzinfo is None:
        # Assume UTC if naive
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    # Normalise to UTC
    dt_utc = dt.astimezone(datetime.timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _parse_timestamp(value: str) -> datetime.datetime:
    """Parse an ISO-8601 UTC timestamp string."""
    # Python < 3.11 does not support fromisoformat with trailing 'Z'
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    if sys.version_info >= (3, 11):
        return datetime.datetime.fromisoformat(value)
    # Fallback for Python 3.9 / 3.10  # pragma: no cover
    try:  # pragma: no cover
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f+00:00")  # pragma: no cover
    except ValueError:  # pragma: no cover
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S+00:00")  # pragma: no cover
