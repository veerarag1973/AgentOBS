"""agentobs.validate — JSON Schema validation for Event envelopes.

This module validates :class:`~agentobs.event.Event` instances against the
published JSON Schema specification. Schema version is selected automatically
from the event's ``schema_version`` field:

* ``"1.0"`` → ``schemas/v1.0/schema.json``
* ``"2.0"`` (default) → ``schemas/v2.0/schema.json``

It uses the optional ``jsonschema`` library when available for full Draft 2020-12
validation.  If ``jsonschema`` is not installed, a lightweight structural check
is performed using only the Python standard library — external dependencies are
strictly optional in line with *agentobs*'s zero-required-dependency policy.

Usage
-----
::

    from agentobs import Event, EventType
    from agentobs.validate import validate_event

    event = Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="llm-trace@0.3.1",
        payload={"span_name": "run", "status": "ok"},
    )
    validate_event(event)   # raises SchemaValidationError if invalid

Public API
----------
* :func:`validate_event` — validate an :class:`~agentobs.event.Event`
  against the matching envelope schema (version-aware).
* :func:`load_schema` — load a specific schema version by key.
* :exc:`~agentobs.exceptions.SchemaValidationError` — raised on validation
  failure (re-exported from :mod:`agentobs.exceptions`).
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from agentobs.event import Event
from agentobs.exceptions import EventTypeError, SchemaValidationError
from agentobs.types import is_registered, validate_custom

__all__: list[str] = ["load_schema", "validate_event"]

# ---------------------------------------------------------------------------
# Schema paths — version-aware (RFC-0001 §15.5)
# ---------------------------------------------------------------------------

_SCHEMAS_DIR: pathlib.Path = pathlib.Path(__file__).parent.parent / "schemas"

#: Map of schema-version strings to their JSON Schema file paths.
_SCHEMA_PATHS: dict[str, pathlib.Path] = {
    "1.0": _SCHEMAS_DIR / "v1.0" / "schema.json",
    "2.0": _SCHEMAS_DIR / "v2.0" / "schema.json",
}

#: Default (current) schema version (RFC-0001-AGENTOBS-Enterprise-2.0).
_DEFAULT_SCHEMA_VERSION: str = "2.0"

# Legacy single-path alias kept for backwards-compatible callers.
_SCHEMA_PATH: pathlib.Path = _SCHEMA_PATHS["1.0"]

# ---------------------------------------------------------------------------
# Compiled patterns from schema (stdlib fallback)
# ---------------------------------------------------------------------------

# RFC-0001 §6.3 — first char 0-7 (timestamp MSB constraint)
_ULID_RE: re.Pattern[str] = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
# RFC-0001 §15.5 — only 1.0 and 2.0 are accepted schema versions.
_ACCEPTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0", "2.0"})
_EVENT_TYPE_RE: re.Pattern[str] = re.compile(
    r"^(?:llm\.(?:trace|cost|cache|eval|guard|fence|prompt|redact|diff|template|audit)\.(?:[a-z][a-z0-9_]*|[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)|(?!llm\.)[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)$"  # NOSONAR — RFC §7 grammar with registered llm namespaces
)
# RFC-0001 §6.1 — microsecond precision mandatory (exactly 6 decimal places)
_TIMESTAMP_RE: re.Pattern[str] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)
# RFC-0001 §5.1 — source: letter start, letters/digits/._-, then @semver
_SOURCE_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9._\-]*@\d+\.\d+\.\d+(?:[.\-][a-zA-Z0-9.]+)?$"
)
_TRACE_ID_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{16}$")
# Checksum and signature carry distinct prefix indicators set by signing.py.
_CHECKSUM_RE: re.Pattern[str] = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE_RE: re.Pattern[str] = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_MAX_TAG_KEYS: int = 50

# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------

_CACHED_SCHEMAS: dict[str, dict[str, Any]] = {}

# Legacy alias kept for call sites that used the old single-schema API.
_CACHED_SCHEMA: dict[str, Any] | None = None


def load_schema(version: str | None = None) -> dict[str, Any]:
    """Load and cache a JSON Schema from disk by version.

    Parameters
    ----------
    version:
        Schema version string, e.g. ``"1.0"`` or ``"2.0"``.
        Defaults to the current SDK schema version (``"2.0"``; RFC §15.5).

    Returns:
    -------
    dict
        Parsed JSON Schema as a plain Python dict.

    Raises:
    ------
    FileNotFoundError
        If the requested schema file cannot be found relative to the
        package root.  This should never happen in a correctly installed
        distribution.
    ValueError
        If an unknown schema version is requested.
    """
    resolved = version or _DEFAULT_SCHEMA_VERSION
    if resolved in _CACHED_SCHEMAS:
        return _CACHED_SCHEMAS[resolved]

    # RFC-0001 §15.5: unknown schema versions MUST raise and stop processing.
    path = _SCHEMA_PATHS.get(resolved)
    if path is None:
        raise ValueError(
            f"Unknown schema version {resolved!r}. "
            f"Available versions: {list(_SCHEMA_PATHS)}"
        )

    if not path.is_file():
        raise FileNotFoundError(
            f"JSON Schema not found at {path}.  "
            "Ensure the 'schemas/' directory is included in the "
            "installed package."
        )
    with path.open("r", encoding="utf-8") as fh:
        schema = json.load(fh)
    _CACHED_SCHEMAS[resolved] = schema
    return schema


# ---------------------------------------------------------------------------
# Internal: stdlib structural validation
# ---------------------------------------------------------------------------


def _check_string_field(
    doc: dict[str, Any],
    field: str,
    *,
    required: bool = True,
    pattern: re.Pattern[str] | None = None,
    min_length: int = 1,
) -> None:
    """Validate a single string field in *doc*."""
    if field not in doc:
        if required:
            raise SchemaValidationError(
                field=field,
                received=None,
                reason=f"required field '{field}' is missing",
            )
        return
    value = doc[field]
    if not isinstance(value, str):
        raise SchemaValidationError(
            field=field,
            received=value,
            reason=f"'{field}' must be a string",
        )
    if len(value) < min_length:
        raise SchemaValidationError(
            field=field,
            received=value,
            reason=f"'{field}' must be at least {min_length} character(s)",
        )
    if pattern is not None and not pattern.match(value):
        raise SchemaValidationError(
            field=field,
            received=value,
            reason=f"'{field}' does not match pattern {pattern.pattern!r}",
        )


def _validate_tags(tags: Any) -> None:
    """Validate the tags dict; raise SchemaValidationError on any violation."""
    if not isinstance(tags, dict):
        raise SchemaValidationError(
            field="tags",
            received=tags,
            reason="'tags' must be an object",
        )
    if len(tags) > _MAX_TAG_KEYS:
        raise SchemaValidationError(
            field="tags",
            received=tags,
            reason=f"'tags' must contain at most {_MAX_TAG_KEYS} keys",
        )
    for k, v in tags.items():
        if not isinstance(k, str) or not k:
            raise SchemaValidationError(
                field=f"tags.{k!r}",
                received=k,
                reason="tag key must be a non-empty string",
            )
        if not isinstance(v, str) or not v:
            raise SchemaValidationError(
                field=f"tags.{k}",
                received=v,
                reason="tag value must be a non-empty string",
            )


def _stdlib_validate(doc: dict[str, Any]) -> None:
    """Perform structural validation without the ``jsonschema`` library.

    Checks required fields, types, and regex patterns as per the published
    JSON Schema spec.  Raises :exc:`~agentobs.exceptions.SchemaValidationError`
    on the first violation found.
    """
    if not isinstance(doc, dict):
        raise SchemaValidationError(
            field="<root>",
            received=doc,
            reason="event must serialise to a JSON object",
        )

    _check_string_field(doc, "schema_version")
    if doc["schema_version"] not in _ACCEPTED_SCHEMA_VERSIONS:
        raise SchemaValidationError(
            field="schema_version",
            received=doc["schema_version"],
            reason=f"'schema_version' must be one of {sorted(_ACCEPTED_SCHEMA_VERSIONS)!r}",
        )
    _check_string_field(doc, "event_id", pattern=_ULID_RE)
    _check_string_field(doc, "event_type", pattern=_EVENT_TYPE_RE)
    if not is_registered(doc["event_type"]):
        try:
            validate_custom(doc["event_type"])
        except EventTypeError as exc:
            raise SchemaValidationError(
                field="event_type",
                received=doc["event_type"],
                reason=str(exc),
            ) from exc
    _check_string_field(doc, "timestamp", pattern=_TIMESTAMP_RE)
    _check_string_field(doc, "source", pattern=_SOURCE_RE)

    # payload
    if "payload" not in doc:
        raise SchemaValidationError(
            field="payload",
            received=None,
            reason="required field 'payload' is missing",
        )
    if not isinstance(doc["payload"], dict) or not doc["payload"]:
        raise SchemaValidationError(
            field="payload",
            received=doc["payload"],
            reason="'payload' must be a non-empty object",
        )

    # Optional tracing fields
    for span_field in ("span_id", "parent_span_id"):
        _check_string_field(doc, span_field, required=False, pattern=_SPAN_ID_RE)
    _check_string_field(doc, "trace_id", required=False, pattern=_TRACE_ID_RE)

    # Optional context fields
    for ctx_field in ("org_id", "team_id", "actor_id", "session_id"):
        _check_string_field(doc, ctx_field, required=False, min_length=1)

    # Optional integrity fields — checksum and signature use distinct prefix patterns.
    _check_string_field(doc, "checksum", required=False, pattern=_CHECKSUM_RE)
    _check_string_field(doc, "signature", required=False, pattern=_SIGNATURE_RE)
    _check_string_field(doc, "prev_id", required=False, pattern=_ULID_RE)

    # tags
    if "tags" in doc:
        _validate_tags(doc["tags"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_event(event: Event) -> None:
    """Validate *event* against the published v1.0 JSON Schema.

    Serialises *event* to a plain dict and validates the envelope structure.
    When the optional ``jsonschema`` package is installed, full Draft 2020-12
    validation is performed.  Otherwise a stdlib-only structural check is run
    that covers all required fields, types, and regex patterns.

    Parameters
    ----------
    event:
        The :class:`~agentobs.event.Event` instance to validate.

    Raises:
    ------
    SchemaValidationError
        If the event does not conform to the envelope schema.
    FileNotFoundError
        If the schema file is missing from the installed distribution.

    Examples:
    --------
    ::

        from agentobs import Event, EventType
        from agentobs.validate import validate_event

        event = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="llm-trace@0.3.1",
            payload={"span_name": "run", "status": "ok"},
        )
        validate_event(event)  # passes silently
    """
    if not isinstance(event, Event):
        raise TypeError(f"validate_event() expects an Event instance, got {type(event)!r}")

    doc = event.to_dict()

    # Select schema version from event envelope (RFC §15.5).
    schema_version: str = doc.get("schema_version") or _DEFAULT_SCHEMA_VERSION

    try:
        import jsonschema  # noqa: PLC0415  (optional import)
        import jsonschema.exceptions  # noqa: PLC0415

        schema = load_schema(schema_version)
        try:
            jsonschema.validate(instance=doc, schema=schema)
        except jsonschema.exceptions.ValidationError as exc:
            # Convert jsonschema's error into our domain error.
            field_path = ".".join(str(part) for part in exc.absolute_path) or "<root>"
            raise SchemaValidationError(
                field=field_path,
                received=exc.instance,
                reason=exc.message,
            ) from exc

    except ImportError:
        # jsonschema not installed — fall back to stdlib structural check.
        _stdlib_validate(doc)
