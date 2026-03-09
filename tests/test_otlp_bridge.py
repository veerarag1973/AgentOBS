"""Tests for agentobs.export.otlp_bridge — Span → OTLP dict translation.

Covers:
- span_to_otlp_dict output shape and field values
- Attribute type encoding (_string_attr, _int_attr, _bool_attr, _double_attr)
- SpanOTLPBridge.to_resource_spans envelope structure
- OTLP format compliance (traceId, spanId, timestamps as strings)
- Token usage and error field mapping
- SpanEvent translation
- Parent span linking
"""

from __future__ import annotations

import pytest

from agentobs._span import Span
from agentobs.export.otlp_bridge import (
    SpanOTLPBridge,
    _bool_attr,
    _double_attr,
    _int_attr,
    _string_attr,
    _to_otlp_attr,
    span_to_otlp_dict,
)
from agentobs.namespaces.trace import SpanEvent, TokenUsage


# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------


class TestAttrHelpers:
    def test_string_attr(self) -> None:
        a = _string_attr("my.key", "my-value")
        assert a == {"key": "my.key", "value": {"stringValue": "my-value"}}

    def test_int_attr(self) -> None:
        a = _int_attr("count", 42)
        assert a == {"key": "count", "value": {"intValue": "42"}}

    def test_bool_attr_true(self) -> None:
        a = _bool_attr("flag", True)
        assert a == {"key": "flag", "value": {"boolValue": True}}

    def test_bool_attr_false(self) -> None:
        a = _bool_attr("flag", False)
        assert a == {"key": "flag", "value": {"boolValue": False}}

    def test_double_attr(self) -> None:
        a = _double_attr("temp", 0.7)
        assert a == {"key": "temp", "value": {"doubleValue": 0.7}}

    def test_to_otlp_attr_string(self) -> None:
        a = _to_otlp_attr("k", "hello")
        assert a["value"] == {"stringValue": "hello"}

    def test_to_otlp_attr_int(self) -> None:
        a = _to_otlp_attr("k", 10)
        assert a["value"] == {"intValue": "10"}

    def test_to_otlp_attr_bool(self) -> None:
        a = _to_otlp_attr("k", True)
        assert a["value"] == {"boolValue": True}

    def test_to_otlp_attr_float(self) -> None:
        a = _to_otlp_attr("k", 3.14)
        assert a["value"] == {"doubleValue": 3.14}

    def test_to_otlp_attr_fallback_to_string(self) -> None:
        a = _to_otlp_attr("k", [1, 2, 3])
        assert "stringValue" in a["value"]


# ---------------------------------------------------------------------------
# span_to_otlp_dict — shape
# ---------------------------------------------------------------------------


def _make_span(**kwargs) -> Span:
    span = Span(name=kwargs.pop("name", "test-span"), **kwargs)
    span.end()
    return span


class TestSpanToOtlpDict:
    def test_required_keys_present(self) -> None:
        s = _make_span()
        d = span_to_otlp_dict(s)
        for key in ("traceId", "spanId", "name", "kind",
                    "startTimeUnixNano", "endTimeUnixNano",
                    "attributes", "status", "events", "links"):
            assert key in d, f"Missing key: {key}"

    def test_trace_and_span_id_are_strings(self) -> None:
        s = _make_span()
        d = span_to_otlp_dict(s)
        assert isinstance(d["traceId"], str)
        assert isinstance(d["spanId"], str)

    def test_timestamps_are_strings(self) -> None:
        s = _make_span()
        d = span_to_otlp_dict(s)
        assert isinstance(d["startTimeUnixNano"], str)
        assert isinstance(d["endTimeUnixNano"], str)
        # Must be parseable as integers
        int(d["startTimeUnixNano"])
        int(d["endTimeUnixNano"])

    def test_name_matches_span(self) -> None:
        s = _make_span(name="my-span")
        d = span_to_otlp_dict(s)
        assert d["name"] == "my-span"

    def test_trace_id_matches_span(self) -> None:
        s = _make_span()
        d = span_to_otlp_dict(s)
        assert d["traceId"] == s.trace_id

    def test_span_id_matches_span(self) -> None:
        s = _make_span()
        d = span_to_otlp_dict(s)
        assert d["spanId"] == s.span_id

    def test_model_becomes_gen_ai_request_model(self) -> None:
        s = _make_span(model="gpt-4o")
        d = span_to_otlp_dict(s)
        attrs = {a["key"]: a["value"] for a in d["attributes"]}
        assert attrs["gen_ai.request.model"] == {"stringValue": "gpt-4o"}

    def test_operation_becomes_gen_ai_operation_name(self) -> None:
        s = _make_span(operation="embedding")
        d = span_to_otlp_dict(s)
        attrs = {a["key"]: a["value"] for a in d["attributes"]}
        assert attrs["gen_ai.operation.name"] == {"stringValue": "embedding"}

    def test_temperature_becomes_attribute(self) -> None:
        s = _make_span(temperature=0.7)
        d = span_to_otlp_dict(s)
        attrs = {a["key"]: a["value"] for a in d["attributes"]}
        assert attrs["gen_ai.request.temperature"] == {"doubleValue": 0.7}

    def test_max_tokens_becomes_attribute(self) -> None:
        s = _make_span(max_tokens=1024)
        d = span_to_otlp_dict(s)
        attrs = {a["key"]: a["value"] for a in d["attributes"]}
        assert attrs["gen_ai.request.max_tokens"] == {"intValue": "1024"}

    def test_token_usage_becomes_attributes(self) -> None:
        s = _make_span()
        s.token_usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )
        s.end()
        d = span_to_otlp_dict(s)
        attrs = {a["key"]: a["value"] for a in d["attributes"]}
        assert attrs["gen_ai.usage.input_tokens"] == {"intValue": "100"}
        assert attrs["gen_ai.usage.output_tokens"] == {"intValue": "50"}
        assert attrs["gen_ai.usage.total_tokens"] == {"intValue": "150"}

    def test_error_status(self) -> None:
        s = _make_span()
        s.status = "error"
        s.error = "something went wrong"
        s.error_type = "ValueError"
        d = span_to_otlp_dict(s)
        assert d["status"]["code"] == 2  # _OTLP_STATUS_ERROR
        assert d["status"]["message"] == "something went wrong"
        attrs = {a["key"]: a["value"] for a in d["attributes"]}
        assert attrs["exception.message"]["stringValue"] == "something went wrong"
        assert attrs["exception.type"]["stringValue"] == "ValueError"

    def test_ok_status(self) -> None:
        s = _make_span()
        s.status = "ok"
        d = span_to_otlp_dict(s)
        assert d["status"]["code"] == 1  # _OTLP_STATUS_OK

    def test_parent_span_id_present_when_set(self) -> None:
        s = _make_span(parent_span_id="abcdef1234567890")
        d = span_to_otlp_dict(s)
        assert d["parentSpanId"] == "abcdef1234567890"

    def test_parent_span_id_absent_when_none(self) -> None:
        s = _make_span()
        d = span_to_otlp_dict(s)
        assert "parentSpanId" not in d

    def test_custom_attributes_included(self) -> None:
        s = _make_span(attributes={"env": "prod", "retry": 3})
        d = span_to_otlp_dict(s)
        keys = [a["key"] for a in d["attributes"]]
        assert "env" in keys
        assert "retry" in keys

    def test_span_events_translated(self) -> None:
        s = _make_span()
        s.events = [SpanEvent(name="cache-hit", metadata={"hit": True})]
        d = span_to_otlp_dict(s)
        assert len(d["events"]) == 1
        ev = d["events"][0]
        assert ev["name"] == "cache-hit"
        assert any(a["key"] == "hit" for a in ev["attributes"])

    def test_open_span_uses_current_time(self) -> None:
        """span_to_otlp_dict works on a span that hasn't been ended yet."""
        s = Span(name="open-span")
        # Do NOT call s.end()
        d = span_to_otlp_dict(s)
        assert int(d["endTimeUnixNano"]) > 0

    def test_links_is_empty_list(self) -> None:
        s = _make_span()
        d = span_to_otlp_dict(s)
        assert d["links"] == []


# ---------------------------------------------------------------------------
# SpanOTLPBridge
# ---------------------------------------------------------------------------


class TestSpanOTLPBridge:
    def test_default_service_name(self) -> None:
        bridge = SpanOTLPBridge()
        assert bridge.service_name == "agentobs"

    def test_custom_service_name(self) -> None:
        bridge = SpanOTLPBridge(service_name="my-service")
        assert bridge.service_name == "my-service"

    def test_to_resource_spans_envelope_keys(self) -> None:
        bridge = SpanOTLPBridge(service_name="test-svc")
        s = _make_span(name="env-span")
        payload = bridge.to_resource_spans([s])
        assert "resourceSpans" in payload
        rs = payload["resourceSpans"]
        assert len(rs) == 1
        assert "resource" in rs[0]
        assert "scopeSpans" in rs[0]

    def test_service_name_in_resource_attributes(self) -> None:
        bridge = SpanOTLPBridge(service_name="my-svc")
        s = _make_span()
        payload = bridge.to_resource_spans([s])
        resource_attrs = {
            a["key"]: a["value"]
            for a in payload["resourceSpans"][0]["resource"]["attributes"]
        }
        assert resource_attrs["service.name"] == {"stringValue": "my-svc"}

    def test_service_version_in_resource_attributes(self) -> None:
        bridge = SpanOTLPBridge(service_name="svc", service_version="2.0.0")
        s = _make_span()
        payload = bridge.to_resource_spans([s])
        resource_attrs = {
            a["key"]: a["value"]
            for a in payload["resourceSpans"][0]["resource"]["attributes"]
        }
        assert resource_attrs["service.version"] == {"stringValue": "2.0.0"}

    def test_service_version_absent_when_not_set(self) -> None:
        bridge = SpanOTLPBridge(service_name="svc")
        s = _make_span()
        payload = bridge.to_resource_spans([s])
        keys = [a["key"] for a in payload["resourceSpans"][0]["resource"]["attributes"]]
        assert "service.version" not in keys

    def test_scope_name_and_version(self) -> None:
        bridge = SpanOTLPBridge()
        s = _make_span()
        payload = bridge.to_resource_spans([s])
        scope = payload["resourceSpans"][0]["scopeSpans"][0]["scope"]
        assert scope["name"] == "agentobs"
        assert scope["version"] == "1.0.7"

    def test_multiple_spans_in_payload(self) -> None:
        bridge = SpanOTLPBridge()
        spans = [_make_span(name=f"span-{i}") for i in range(3)]
        payload = bridge.to_resource_spans(spans)
        otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert len(otlp_spans) == 3
        names = [s["name"] for s in otlp_spans]
        assert names == ["span-0", "span-1", "span-2"]

    def test_empty_span_list(self) -> None:
        bridge = SpanOTLPBridge()
        payload = bridge.to_resource_spans([])
        otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert otlp_spans == []

    def test_importable_from_agentobs(self) -> None:
        from agentobs import SpanOTLPBridge as B, span_to_otlp_dict as fn  # noqa: PLC0415

        assert B is SpanOTLPBridge
        assert fn is span_to_otlp_dict
