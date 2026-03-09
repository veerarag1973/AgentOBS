"""OTLP-compatible JSON exporter for agentobs events.

Produces OTLP/JSON payloads (spans *or* log records) that can be forwarded to
any OTLP collector (Datadog, Grafana Tempo, Honeycomb, Elastic, Splunk, …).

**No opentelemetry-sdk dependency** — this module builds the OTLP wire format
from the stdlib only.  If you already have the OTel SDK installed you can pipe
the output through the SDK's exporters as a dict; the schema is 1-to-1.

Format selection
----------------
* Event **with** ``trace_id``  →  OTLP *span*  (``resourceSpans``).
* Event **without** ``trace_id`` →  OTLP *log record* (``resourceLogs``).

Performance
-----------
Serialisation of 500 events is well under 200 ms (target: < 200 ms) because
every field mapping is a pure Python dict operation with no I/O on the hot path.
Network I/O is isolated in :meth:`OTLPExporter._send` and runs in a thread-pool
executor so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agentobs.exceptions import ExportError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentobs.event import Event

__all__ = ["OTLPExporter", "ResourceAttributes", "extract_trace_context", "make_traceparent"]

# Scope name embedded in every OTLP payload (instrumentation scope).
_SCOPE_NAME = "agentobs"

# Hex-string lengths for W3C TraceContext IDs.
_TRACE_ID_HEX_LEN = 32
_SPAN_ID_HEX_LEN = 16

_FINISH_REASONS_KEY = "gen_ai.response.finish_reasons"
_TRACEPARENT_PARTS_COUNT = 4


def _is_private_ip_literal(host: str) -> bool:
    """Return ``True`` if *host* is a private/loopback/link-local **literal** IP.

    DNS hostnames are **not** resolved.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast


def _validate_http_url(
    url: str,
    param_name: str = "url",
    *,
    allow_private_addresses: bool = False,
) -> None:
    """Raise *ValueError* if *url* is not a valid ``http://`` or ``https://`` URL."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"{param_name} must be a valid http:// or https:// URL; got {url!r}"
        )
    if not allow_private_addresses:
        host = parsed.hostname or ""
        if _is_private_ip_literal(host):
            raise ValueError(
                f"{param_name} resolves to a private/loopback/link-local IP address "
                f"({host!r}).  Set allow_private_addresses=True to permit this."
            )


# ---------------------------------------------------------------------------
# Resource attributes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceAttributes:
    """OTel resource attributes attached to every exported payload.

    Attributes:
        service_name:           Value for the ``service.name`` resource attr.
        deployment_environment: Value for ``deployment.environment``.
        extra:                  Additional arbitrary resource attributes.

    Example::

        res = ResourceAttributes(
            service_name="my-service",
            deployment_environment="staging",
            extra={"k8s.namespace": "default"},
        )
    """

    service_name: str
    deployment_environment: str = "production"
    extra: dict[str, str] = field(default_factory=dict)

    def to_otlp(self) -> list[dict[str, Any]]:
        """Return a list of OTLP ``KeyValue`` dicts for the resource."""
        attrs: list[dict[str, Any]] = [
            _kv("service.name", self.service_name),
            # deployment.environment.name supersedes deployment.environment (semconv 1.21+)
            _kv("deployment.environment.name", self.deployment_environment),
        ]
        for k, v in self.extra.items():
            attrs.append(_kv(k, v))
        return attrs


# ---------------------------------------------------------------------------
# OTLP wire-format helpers
# ---------------------------------------------------------------------------


def _kv(key: str, value: Any) -> dict[str, Any]:  # noqa: ANN401
    """Build an OTLP ``{key, value}`` attribute dict."""
    return {"key": key, "value": _otlp_value(value)}


def _otlp_value(v: Any) -> dict[str, Any]:  # noqa: ANN401
    """Wrap a Python scalar in the appropriate OTLP ``AnyValue`` dict."""
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        # OTLP int64 is encoded as a JSON string to preserve precision.
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _ts_to_unix_nano(ts: str) -> int:
    """Convert an ISO-8601 UTC timestamp string to nanoseconds since epoch.

    Supports both ``Z`` and ``+00:00`` suffixes.  Microsecond precision is
    preserved; fractional nanoseconds are truncated.

    Args:
        ts: ISO-8601 UTC string, e.g. ``"2024-01-15T12:34:56.789012Z"``.

    Returns:
        Integer nanoseconds since the Unix epoch.
    """
    normalised = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalised)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = dt - epoch
    # total_seconds() gives float with microsecond resolution; scale to ns.
    return int(delta.total_seconds() * 1_000_000_000)


def _derive_span_id(event_id: str) -> str:
    """Derive a valid 16-hex-char span ID from a ULID event ID.

    Used as a fallback when the event carries no explicit ``span_id``.
    The derivation is deterministic so the same event always maps to the
    same synthetic span ID.

    Args:
        event_id: A 26-character ULID string.

    Returns:
        16-character lower-case hex string.
    """
    return hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# OpenTelemetry semantic convention helpers
# ---------------------------------------------------------------------------

# OTLP StatusCode integers (google.rpc.Status / OTLP spec).
_STATUS_CODE_OK = 1
_STATUS_CODE_ERROR = 2


def _gen_ai_model_attrs(model: dict[str, Any], attrs: list[dict[str, Any]]) -> None:
    """Append gen_ai.system / gen_ai.request.model attrs from model dict."""
    provider = model.get("provider")
    if provider:
        attrs.append(_kv("gen_ai.system", str(provider)))
    name = model.get("name")
    if name:
        attrs.append(_kv("gen_ai.request.model", str(name)))
    version = model.get("version")
    if version:
        attrs.append(_kv("gen_ai.request.model_version", str(version)))


def _gen_ai_token_attrs(token_usage: dict[str, Any], attrs: list[dict[str, Any]]) -> None:
    """Append gen_ai usage token attrs from token_usage dict."""
    prompt_tokens = token_usage.get("prompt_tokens")
    if prompt_tokens is not None:
        attrs.append(_kv("gen_ai.usage.input_tokens", int(prompt_tokens)))
    completion_tokens = token_usage.get("completion_tokens")
    if completion_tokens is not None:
        attrs.append(_kv("gen_ai.usage.output_tokens", int(completion_tokens)))


def _gen_ai_attributes(event: Event) -> list[dict[str, Any]]:
    """Build ``gen_ai.*`` OpenTelemetry GenAI semantic convention attributes.

    Maps model info, token usage, and operation metadata from the event payload
    to the standard OTel GenAI semconv namespace (semconv 1.27+).

    See: https://opentelemetry.io/docs/specs/semconv/gen-ai/

    Args:
        event: The event whose payload is inspected.

    Returns:
        A (possibly empty) list of OTLP ``KeyValue`` dicts.
    """
    attrs: list[dict[str, Any]] = []
    payload = event.payload

    span_name = payload.get("span_name")
    if span_name:
        attrs.append(_kv("gen_ai.operation.name", str(span_name)))

    model = payload.get("model")
    if isinstance(model, dict):
        _gen_ai_model_attrs(model, attrs)

    token_usage = payload.get("token_usage")
    if isinstance(token_usage, dict):
        _gen_ai_token_attrs(token_usage, attrs)

    status = payload.get("status")
    error = payload.get("error")
    if status == "error" or error:
        attrs.append(_kv(_FINISH_REASONS_KEY, "error"))
    elif status == "timeout":
        attrs.append(_kv(_FINISH_REASONS_KEY, "timeout"))
    elif status == "ok":
        attrs.append(_kv(_FINISH_REASONS_KEY, "stop"))

    return attrs


def _map_span_status(event: Event) -> dict[str, Any]:
    """Map event payload ``status`` to an OTLP ``SpanStatus`` dict.

    ``"error"`` and ``"timeout"`` outcomes yield ``STATUS_CODE_ERROR`` (2).
    Everything else yields ``STATUS_CODE_OK`` (1).  An error message is
    included when the payload carries an ``error`` field.

    Args:
        event: The event carrying a ``status`` and optional ``error`` field.

    Returns:
        An OTLP ``{code, [message]}`` status dict.
    """
    payload = event.payload
    status = payload.get("status", "ok")
    if status in ("error", "timeout"):
        result: dict[str, Any] = {"code": _STATUS_CODE_ERROR}
        error_msg = payload.get("error")
        if error_msg:
            result["message"] = str(error_msg)
        elif status == "timeout":
            result["message"] = "Operation timed out"
        return result
    return {"code": _STATUS_CODE_OK}


def _compute_end_nano(start_nano: int, event: Event) -> int:
    """Compute ``endTimeUnixNano`` from start time plus payload ``duration_ms``.

    If ``duration_ms`` is absent or cannot be parsed, falls back to
    ``start_nano`` (zero-duration span — should only happen for events without
    timing information such as ``span.started`` events).

    Args:
        start_nano: Span start time in nanoseconds since Unix epoch.
        event:      Event that may carry a ``duration_ms`` payload field.

    Returns:
        End time in nanoseconds since Unix epoch.
    """
    duration_ms = event.payload.get("duration_ms")
    if duration_ms is not None:
        try:
            return start_nano + int(float(duration_ms) * 1_000_000)
        except (TypeError, ValueError):
            pass
    return start_nano


def _flatten_payload(
    payload: dict[str, Any],
    prefix: str = "llm.payload",
) -> list[dict[str, Any]]:
    """Recursively flatten a nested dict to OTLP attribute key-value pairs.

    Nested keys are joined with ``"."`` (dot notation).

    Args:
        payload: The dict to flatten.
        prefix:  Key prefix for all emitted attributes.

    Returns:
        A list of OTLP ``KeyValue`` dicts.
    """
    result: list[dict[str, Any]] = []
    for k, v in payload.items():
        full_key = f"{prefix}.{k}"
        if isinstance(v, dict):
            result.extend(_flatten_payload(v, full_key))
        else:
            result.append(_kv(full_key, v))
    return result


def _event_to_attributes(event: Event) -> list[dict[str, Any]]:
    """Build the full OTLP attribute list for an :class:`~agentobs.event.Event`.

    Envelope metadata, identity, tags, integrity fields, and payload are all
    mapped to well-known ``llm.*`` namespace attributes.
    """
    attrs: list[dict[str, Any]] = [
        _kv("llm.schema_version", event.schema_version),
        _kv("llm.event_id", event.event_id),
        _kv("llm.event_type", event.event_type),
        _kv("llm.source", event.source),
    ]

    # Identity fields
    if event.org_id is not None:
        attrs.append(_kv("llm.org_id", event.org_id))
    if event.team_id is not None:
        attrs.append(_kv("llm.team_id", event.team_id))
    if event.actor_id is not None:
        attrs.append(_kv("llm.actor_id", event.actor_id))
    if event.session_id is not None:
        attrs.append(_kv("llm.session_id", event.session_id))

    # Tags
    if event.tags is not None:
        for tag_key, tag_val in event.tags.items():
            attrs.append(_kv(f"llm.tag.{tag_key}", tag_val))

    # Integrity / audit chain fields
    if event.checksum is not None:
        attrs.append(_kv("llm.checksum", event.checksum))
    if event.signature is not None:
        attrs.append(_kv("llm.signature", event.signature))
    if event.prev_id is not None:
        attrs.append(_kv("llm.prev_id", event.prev_id))

    # Flatten payload fields into span/log attributes.
    attrs.extend(_flatten_payload(event.payload))

    # OpenTelemetry GenAI semantic conventions (semconv 1.27+)
    # These sit alongside the llm.* namespace so both ecosystems work.
    attrs.extend(_gen_ai_attributes(event))

    return attrs


# ---------------------------------------------------------------------------
# OTLPExporter
# ---------------------------------------------------------------------------


class OTLPExporter:
    """Async exporter that serialises agentobs events to the OTLP/JSON format.

    Events that carry a ``trace_id`` are emitted as **OTLP spans**
    (``resourceSpans``).  Events without a ``trace_id`` are emitted as **OTLP
    log records** (``resourceLogs``).

    HTTP transport uses :func:`urllib.request.urlopen` inside a thread-pool
    executor so the async event loop is never blocked.

    Args:
        endpoint:       Full OTLP HTTP URL, e.g.
                        ``"http://otel-collector:4318/v1/traces"``.
        headers:        Optional extra HTTP request headers (e.g. API keys).
        resource_attrs: :class:`ResourceAttributes` attached to every payload.
        timeout:        HTTP request timeout in seconds (default 5.0).
        batch_size:     Maximum events per :meth:`export_batch` call (default
                        500).  Larger batches are split automatically.

    Example::

        exporter = OTLPExporter(
            endpoint="http://localhost:4318/v1/traces",
            resource_attrs=ResourceAttributes(service_name="llm-trace"),
        )
        await exporter.export(event)
    """

    def __init__(  # noqa: PLR0913
        self,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        resource_attrs: ResourceAttributes | None = None,
        timeout: float = 5.0,
        batch_size: int = 500,
        allow_private_addresses: bool = False,
    ) -> None:
        if not endpoint:
            raise ValueError("endpoint must be a non-empty string")
        _validate_http_url(endpoint, "endpoint", allow_private_addresses=allow_private_addresses)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._endpoint = endpoint
        self._headers: dict[str, str] = dict(headers) if headers else {}
        self._resource_attrs: ResourceAttributes = resource_attrs or ResourceAttributes(
            service_name="agentobs"
        )
        self._timeout = timeout
        self._batch_size = batch_size

    # ------------------------------------------------------------------
    # Sync mapping API (pure, no I/O — safe to call in hot loops)
    # ------------------------------------------------------------------

    def to_otlp_span(self, event: Event) -> dict[str, Any]:
        """Map a single event to an OTLP span dict.

        If the event has no ``span_id``, a deterministic synthetic ID is derived
        from the ``event_id``.  If the event has no ``trace_id``, a zero-filled
        placeholder is used (``"00…0"``).

        Args:
            event: The :class:`~agentobs.event.Event` to map.

        Returns:
            An OTLP-compatible span dict.
        """
        ts_nano = _ts_to_unix_nano(event.timestamp)
        end_nano = _compute_end_nano(ts_nano, event)
        span_id = event.span_id or _derive_span_id(event.event_id)
        trace_id = event.trace_id or ("0" * 32)

        span: dict[str, Any] = {
            "traceId": trace_id,
            "spanId": span_id,
            "name": event.event_type,
            # SPAN_KIND_CLIENT (3) — LLM calls are outgoing client requests.
            "kind": 3,
            "startTimeUnixNano": str(ts_nano),
            "endTimeUnixNano": str(end_nano),
            "attributes": _event_to_attributes(event),
            "status": _map_span_status(event),
            # Bit 0 set = sampled (W3C TraceContext §7.1.2)
            "traceFlags": 1,
        }
        if event.parent_span_id is not None:
            span["parentSpanId"] = event.parent_span_id

        return span

    def to_otlp_log(self, event: Event) -> dict[str, Any]:
        """Map a single event to an OTLP log record dict.

        Args:
            event: The :class:`~agentobs.event.Event` to map.

        Returns:
            An OTLP-compatible log record dict.
        """
        ts_nano = _ts_to_unix_nano(event.timestamp)

        record: dict[str, Any] = {
            "timeUnixNano": str(ts_nano),
            "observedTimeUnixNano": str(ts_nano),
            "severityNumber": 9,  # SEVERITY_NUMBER_INFO
            "severityText": "INFO",
            "body": {"stringValue": event.event_type},
            "attributes": _event_to_attributes(event),
        }
        # Include tracing context even for log records if present.
        if event.trace_id is not None:
            record["traceId"] = event.trace_id
        if event.span_id is not None:
            record["spanId"] = event.span_id

        return record

    # ------------------------------------------------------------------
    # Async export API
    # ------------------------------------------------------------------

    async def export(self, event: Event) -> dict[str, Any]:
        """Export a single event as an OTLP payload and HTTP POST it.

        Span vs log selection is automatic: events with a ``trace_id`` become
        spans; all others become log records.

        Args:
            event: The event to export.

        Returns:
            The OTLP span or log record dict that was sent.

        Raises:
            ExportError: If the HTTP request fails.
        """
        if event.trace_id is not None:
            record = self.to_otlp_span(event)
            payload = self._wrap_spans([record])
        else:
            record = self.to_otlp_log(event)
            payload = self._wrap_logs([record])

        await self._send(payload)
        return record

    async def export_batch(self, events: Sequence[Event]) -> list[dict[str, Any]]:
        """Export a sequence of events, batching spans and logs separately.

        Spans and log records are split into two HTTP requests so each request
        targets the correct OTLP endpoint format.

        Args:
            events: Sequence of events (at most :attr:`batch_size` per call;
                    larger sequences should be chunked by the caller).

        Returns:
            List of OTLP record dicts (spans first, then log records, in
            original insertion order within each group).

        Raises:
            ExportError: If any HTTP request fails.
        """
        spans: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        # Preserve per-type insertion order for the returned list.
        records: list[dict[str, Any]] = []

        for event in events:
            if event.trace_id is not None:
                r = self.to_otlp_span(event)
                spans.append(r)
            else:
                r = self.to_otlp_log(event)
                logs.append(r)
            records.append(r)

        if spans:
            for i in range(0, len(spans), self._batch_size):
                await self._send(self._wrap_spans(spans[i : i + self._batch_size]))
        if logs:
            for i in range(0, len(logs), self._batch_size):
                await self._send(self._wrap_logs(logs[i : i + self._batch_size]))

        return records

    # ------------------------------------------------------------------
    # OTLP envelope helpers
    # ------------------------------------------------------------------

    def _wrap_spans(self, spans: list[dict[str, Any]]) -> dict[str, Any]:
        """Wrap span records in a ``resourceSpans`` OTLP envelope."""
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": self._resource_attrs.to_otlp()},
                    "scopeSpans": [
                        {
                            "scope": {"name": _SCOPE_NAME},
                            "spans": spans,
                        }
                    ],
                }
            ]
        }

    def _wrap_logs(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        """Wrap log records in a ``resourceLogs`` OTLP envelope."""
        return {
            "resourceLogs": [
                {
                    "resource": {"attributes": self._resource_attrs.to_otlp()},
                    "scopeLogs": [
                        {
                            "scope": {"name": _SCOPE_NAME},
                            "logRecords": logs,
                        }
                    ],
                }
            ]
        }

    # ------------------------------------------------------------------
    # HTTP transport (executor-based, non-blocking)
    # ------------------------------------------------------------------

    async def _send(self, payload: dict[str, Any]) -> None:
        """Serialise *payload* to JSON and POST it to :attr:`_endpoint`.

        Runs in a thread-pool executor so the async event loop is not blocked
        during network I/O.

        Args:
            payload: A fully-built OTLP envelope dict.

        Raises:
            ExportError: On HTTP 4xx/5xx or network errors.
        """
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **self._headers}
        endpoint = self._endpoint
        timeout = self._timeout

        def _do_request() -> None:
            req = urllib.request.Request(  # noqa: S310
                url=endpoint,
                data=body,
                headers=request_headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    resp.read()
            except urllib.error.HTTPError as exc:
                raise ExportError(
                    "otlp",
                    f"HTTP {exc.code}: {exc.reason}",
                ) from exc
            except OSError as exc:
                raise ExportError("otlp", str(exc)) from exc

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_request)

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"OTLPExporter(endpoint={self._endpoint!r}, "
            f"batch_size={self._batch_size!r})"
        )


# ---------------------------------------------------------------------------
# W3C TraceContext utilities  (RFC 9429)
# ---------------------------------------------------------------------------


def make_traceparent(
    trace_id: str,
    span_id: str,
    *,
    sampled: bool = True,
) -> str:
    """Build a W3C ``traceparent`` header value.

    Produces a version-``00`` ``traceparent`` string that can be injected into
    outgoing HTTP requests to propagate distributed trace context.

    Args:
        trace_id: 32 lowercase hex characters (OTel trace ID).
        span_id:  16 lowercase hex characters (current span ID).
        sampled:  Whether the trace is sampled.  Sets the ``sampled`` flag
                  (W3C TraceContext §7.1.2).  Defaults to ``True``.

    Returns:
        A ``traceparent`` header value, e.g.
        ``"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"``.

    Raises:
        ValueError: If ``trace_id`` or ``span_id`` do not match the required
            format.

    Example::

        headers["traceparent"] = make_traceparent(
            event.trace_id, event.span_id
        )
    """
    if len(trace_id) != _TRACE_ID_HEX_LEN or not all(c in "0123456789abcdef" for c in trace_id):
        raise ValueError(
            f"trace_id must be 32 lowercase hex characters; got {trace_id!r}"
        )
    if len(span_id) != _SPAN_ID_HEX_LEN or not all(c in "0123456789abcdef" for c in span_id):
        raise ValueError(
            f"span_id must be 16 lowercase hex characters; got {span_id!r}"
        )
    flags = "01" if sampled else "00"
    return f"00-{trace_id}-{span_id}-{flags}"


def extract_trace_context(  # noqa: PLR0911
    headers: dict[str, str],
) -> dict[str, Any] | None:
    """Extract W3C TraceContext from a ``traceparent`` / ``tracestate`` header dict.

    Parses the incoming ``traceparent`` header (case-insensitive key lookup)
    and returns the extracted trace context.  Returns ``None`` if the header
    is absent or malformed.

    Args:
        headers: A dict of HTTP headers (keys are matched case-insensitively).

    Returns:
        A dict with keys ``trace_id``, ``span_id``, ``sampled`` (bool), and
        optionally ``tracestate`` (str) if that header is present; or ``None``
        if no valid ``traceparent`` was found.

    Example::

        ctx = extract_trace_context(request.headers)
        if ctx:
            event = Event(
                event_type=...,
                source=...,
                payload=...,
                trace_id=ctx["trace_id"],
                parent_span_id=ctx["span_id"],
            )
    """
    # Case-insensitive header lookup.
    lower_headers = {k.lower(): v for k, v in headers.items()}
    traceparent = lower_headers.get("traceparent")
    if not traceparent:
        return None

    parts = traceparent.strip().split("-")
    if len(parts) != _TRACEPARENT_PARTS_COUNT:
        return None
    version, trace_id, parent_span_id, trace_flags_hex = parts
    # Only version 00 is supported (future versions may have more parts).
    if version != "00":
        return None
    if len(trace_id) != _TRACE_ID_HEX_LEN or len(parent_span_id) != _SPAN_ID_HEX_LEN:
        return None
    if not all(c in "0123456789abcdef" for c in trace_id):
        return None
    if not all(c in "0123456789abcdef" for c in parent_span_id):
        return None

    try:
        flags_int = int(trace_flags_hex, 16)
    except ValueError:
        return None

    result: dict[str, Any] = {
        "trace_id": trace_id,
        "span_id": parent_span_id,
        "sampled": bool(flags_int & 0x01),
    }
    tracestate = lower_headers.get("tracestate")
    if tracestate:
        result["tracestate"] = tracestate
    return result
