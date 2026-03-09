"""agentobs.validate — JSON Schema validation for Event envelopes.

This module validates :class:`~agentobs.event.Event` instances against the
published JSON Schema specification in ``schemas/v1.0/schema.json``.

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
  against the v1.0 envelope schema.
* :exc:`~agentobs.exceptions.SchemaValidationError` — raised on validation
  failure (re-exported from :mod:`agentobs.exceptions`).
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from agentobs.event import Event
from agentobs.exceptions import SchemaValidationError

__all__: list[str] = ["load_schema", "validate_event"]

# ---------------------------------------------------------------------------
# Schema path
# ---------------------------------------------------------------------------

#: Absolute path to the published JSON Schema (RFC-0001 v2.0 envelope schema).
_SCHEMA_PATH: pathlib.Path = (
    pathlib.Path(__file__).parent.parent / "docs" / "schema" / "envelope.schema.json"
)

# ---------------------------------------------------------------------------
# Compiled patterns from schema (stdlib fallback)
# ---------------------------------------------------------------------------

# RFC-0001 §6.3 — first char 0-7 (timestamp MSB constraint)
_ULID_RE: re.Pattern[str] = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_SEMVER_RE: re.Pattern[str] = re.compile(
    r"^\d+\.\d+(?:\.\d+)?(?:[.-][a-zA-Z0-9.]+)?$"
)
_EVENT_TYPE_RE: re.Pattern[str] = re.compile(
    r"^(?:llm\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}|[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*){2,}\.[a-z][a-z0-9_]*)$"  # NOSONAR — complex but spec-required pattern
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

# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------

_CACHED_SCHEMA: dict[str, Any] | None = None


def load_schema() -> dict[str, Any]:
    """Load and cache the v1.0 JSON Schema from disk.

    Returns:
    -------
    dict
        Parsed JSON Schema as a plain Python dict.

    Raises:
    ------
    FileNotFoundError
        If ``schemas/v1.0/schema.json`` cannot be found relative to the
        package root.  This should never happen in a correctly installed
        distribution.
    """
    global _CACHED_SCHEMA  # noqa: PLW0603
    if _CACHED_SCHEMA is None:
        if not _SCHEMA_PATH.is_file():
            raise FileNotFoundError(
                f"JSON Schema not found at {_SCHEMA_PATH}.  "
                "Ensure the 'schemas/' directory is included in the "
                "installed package."
            )
        with _SCHEMA_PATH.open("r", encoding="utf-8") as fh:
            _CACHED_SCHEMA = json.load(fh)
    return _CACHED_SCHEMA


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

    _check_string_field(doc, "schema_version", pattern=_SEMVER_RE)
    _check_string_field(doc, "event_id", pattern=_ULID_RE)
    _check_string_field(doc, "event_type", pattern=_EVENT_TYPE_RE)
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

    try:
        import jsonschema  # noqa: PLC0415  (optional import)
        import jsonschema.exceptions  # noqa: PLC0415

        schema = load_schema()
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
