"""agentobs.export.otlp_bridge — Lightweight Span → OTLP dict translation.

Translates :class:`~agentobs._span.Span` instances into the OTLP ``ReadableSpan``
dict format **without** requiring the ``opentelemetry-sdk`` package.  The produced
dicts are wire-compatible with the OTLP/JSON specification and can be embedded
directly into ``resourceSpans[].scopeSpans[].spans`` payloads.

This complements :mod:`agentobs.export.otel_bridge` (which bridges to the OTel
SDK's ``TracerProvider``) by providing a zero-dependency serialisation path for
testing, custom exporters, and SDK-less environments.

Usage::

    from agentobs.export.otlp_bridge import span_to_otlp_dict, SpanOTLPBridge

    # Single span → OTLP dict (no OTel SDK required)
    otlp = span_to_otlp_dict(my_span)
    print(otlp["name"], otlp["traceId"], otlp["spanId"])

    # Full OTLP resource-spans envelope
    bridge = SpanOTLPBridge(service_name="my-agent")
    payload = bridge.to_resource_spans([span1, span2])
    # → {"resourceSpans": [...]}
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agentobs._span import Span

__all__ = ["SpanOTLPBridge", "span_to_otlp_dict"]

# OTLP SpanKind integer constants (opentelemetry.proto.trace.v1.Span.SpanKind).
_OTLP_SPAN_KIND_UNSPECIFIED = 0  # noqa: F841
_OTLP_SPAN_KIND_INTERNAL = 1
_OTLP_SPAN_KIND_SERVER = 2  # noqa: F841
_OTLP_SPAN_KIND_CLIENT = 3
_OTLP_SPAN_KIND_PRODUCER = 4  # noqa: F841
_OTLP_SPAN_KIND_CONSUMER = 5  # noqa: F841

# OTLP Status codes (opentelemetry.proto.trace.v1.Status.StatusCode).
_OTLP_STATUS_UNSET = 0  # noqa: F841
_OTLP_STATUS_OK = 1
_OTLP_STATUS_ERROR = 2

_SCOPE_NAME = "agentobs"
_SCOPE_VERSION = "1.0.7"


# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------


def _string_attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _int_attr(key: str, value: int) -> dict[str, Any]:
    return {"key": key, "value": {"intValue": str(value)}}


def _bool_attr(key: str, value: bool) -> dict[str, Any]:
    return {"key": key, "value": {"boolValue": value}}


def _double_attr(key: str, value: float) -> dict[str, Any]:
    return {"key": key, "value": {"doubleValue": value}}


def _to_otlp_attr(key: str, value: Any) -> dict[str, Any]:  # noqa: ANN401
    """Convert a single key-value pair to an OTLP-format attribute dict."""
    if isinstance(value, bool):
        return _bool_attr(key, value)
    if isinstance(value, int):
        return _int_attr(key, value)
    if isinstance(value, float):
        return _double_attr(key, value)
    return _string_attr(key, str(value))


# ---------------------------------------------------------------------------
# span_to_otlp_dict
# ---------------------------------------------------------------------------


def span_to_otlp_dict(span: "Span") -> dict[str, Any]:
    """Translate a :class:`~agentobs._span.Span` to an OTLP span dict.

    The returned dict conforms to the OTLP/JSON ``Span`` protobuf shape
    (``opentelemetry.proto.trace.v1.Span``).  All nanosecond timestamps are
    serialised as **strings** per the OTLP/JSON spec.

    Args:
        span: A :class:`~agentobs._span.Span` instance (open or closed).
              When ``end_ns`` is ``None`` the current time is substituted.

    Returns:
        OTLP-format ``dict`` ready to embed in a ``resourceSpans`` payload.
    """
    import time  # noqa: PLC0415

    start_ns = span.start_ns
    end_ns = span.end_ns if span.end_ns is not None else time.time_ns()

    # Build attributes list following gen_ai.* semantic conventions.
    attrs: list[dict[str, Any]] = []
    if span.model:
        attrs.append(_string_attr("gen_ai.request.model", span.model))
    if span.operation:
        attrs.append(_string_attr("gen_ai.operation.name", str(span.operation)))
    if span.temperature is not None:
        attrs.append(_double_attr("gen_ai.request.temperature", span.temperature))
    if span.top_p is not None:
        attrs.append(_double_attr("gen_ai.request.top_p", span.top_p))
    if span.max_tokens is not None:
        attrs.append(_int_attr("gen_ai.request.max_tokens", span.max_tokens))
    if span.token_usage is not None:
        tu = span.token_usage
        if tu.input_tokens is not None:
            attrs.append(_int_attr("gen_ai.usage.input_tokens", tu.input_tokens))
        if tu.output_tokens is not None:
            attrs.append(_int_attr("gen_ai.usage.output_tokens", tu.output_tokens))
        if tu.total_tokens is not None:
            attrs.append(_int_attr("gen_ai.usage.total_tokens", tu.total_tokens))
    if span.error:
        attrs.append(_string_attr("exception.message", span.error))
    if span.error_type:
        attrs.append(_string_attr("exception.type", span.error_type))
    if span.error_category:
        attrs.append(_string_attr("agentobs.error.category", span.error_category))
    for k, v in (span.attributes or {}).items():
        attrs.append(_to_otlp_attr(str(k), v))

    # Status code mapping.
    status_code = _OTLP_STATUS_ERROR if span.status == "error" else _OTLP_STATUS_OK
    otlp_status: dict[str, Any] = {"code": status_code}
    if span.error:
        otlp_status["message"] = span.error

    result: dict[str, Any] = {
        "traceId": span.trace_id,
        "spanId": span.span_id,
        "name": span.name,
        "kind": _OTLP_SPAN_KIND_CLIENT,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attrs,
        "status": otlp_status,
        "events": [],
        "links": [],
    }

    if span.parent_span_id:
        result["parentSpanId"] = span.parent_span_id

    # Translate SpanEvent list to OTLP events.
    for ev in (span.events or []):
        ev_attrs = [_to_otlp_attr(str(k), v) for k, v in (ev.metadata or {}).items()]
        result["events"].append({
            "name": ev.name,
            "timeUnixNano": str(start_ns),  # SpanEvent has no own timestamp; use span start
            "attributes": ev_attrs,
        })

    return result


# ---------------------------------------------------------------------------
# SpanOTLPBridge
# ---------------------------------------------------------------------------


class SpanOTLPBridge:
    """Assembles OTLP ``resourceSpans`` payloads from :class:`~agentobs._span.Span` lists.

    Usage::

        bridge = SpanOTLPBridge(service_name="my-agent", service_version="1.0.0")
        payload = bridge.to_resource_spans([span1, span2])
        # → {"resourceSpans": [...]}

    Args:
        service_name:    Value for the ``service.name`` resource attribute.
        service_version: Optional value for the ``service.version`` resource attribute.
    """

    def __init__(
        self,
        service_name: str = "agentobs",
        service_version: str | None = None,
    ) -> None:
        self.service_name = service_name
        self.service_version = service_version

    def to_resource_spans(self, spans: list["Span"]) -> dict[str, Any]:
        """Build a complete OTLP/JSON ``resourceSpans`` envelope from *spans*.

        Args:
            spans: List of :class:`~agentobs._span.Span` objects to serialise.

        Returns:
            ``{"resourceSpans": [...]}`` dict for JSON serialisation or forwarding
            to an OTLP collector.
        """
        resource_attrs: list[dict[str, Any]] = [
            _string_attr("service.name", self.service_name),
        ]
        if self.service_version:
            resource_attrs.append(_string_attr("service.version", self.service_version))

        otlp_spans = [span_to_otlp_dict(s) for s in spans]

        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": resource_attrs},
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": _SCOPE_NAME,
                                "version": _SCOPE_VERSION,
                            },
                            "spans": otlp_spans,
                        }
                    ],
                }
            ]
        }
