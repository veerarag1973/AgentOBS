"""Tests for agentobs/export/otlp.py — OTLPExporter.

Coverage targets
----------------
* All public methods: ``to_otlp_span``, ``to_otlp_log``, ``export``,
  ``export_batch``, ``_wrap_spans``, ``_wrap_logs``, ``_send``.
* All private helpers: ``_kv``, ``_otlp_value``, ``_ts_to_unix_nano``,
  ``_derive_span_id``, ``_flatten_payload``, ``_event_to_attributes``.
* ``ResourceAttributes.to_otlp`` with and without extra attrs.
* Error paths in ``_send`` (HTTPError, OSError).
* Both span and log code paths in ``export`` / ``export_batch``.
* Performance: serialisation of 500 events < 200 ms.
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentobs.event import Event, Tags
from agentobs.exceptions import ExportError
from agentobs.export.otlp import (
    OTLPExporter,
    ResourceAttributes,
    _compute_end_nano,
    _derive_span_id,
    _event_to_attributes,
    _flatten_payload,
    _gen_ai_attributes,
    _kv,
    _map_span_status,
    _otlp_value,
    _ts_to_unix_nano,
    extract_trace_context,
    make_traceparent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(  # noqa: PLR0913
    *,
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
    payload: dict | None = None,
) -> Event:
    return Event(
        event_type="llm.trace.span.completed",
        source="test-tool@1.0.0",
        payload=payload or {"status": "ok", "tokens": 42},
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        org_id=org_id,
        team_id=team_id,
        actor_id=actor_id,
        session_id=session_id,
        tags=tags,
        checksum=checksum,
        signature=signature,
        prev_id=prev_id,
    )


def _make_exporter(**kwargs: Any) -> OTLPExporter:
    return OTLPExporter("http://localhost:4318/v1/traces", **kwargs)  # NOSONAR


def _mock_urlopen_success() -> MagicMock:
    """Return a mock context manager that simulates a successful HTTP 200."""
    resp = MagicMock()
    resp.read.return_value = b""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# _otlp_value
# ---------------------------------------------------------------------------


class TestOtlpValue:
    def test_bool_true(self) -> None:
        assert _otlp_value(True) == {"boolValue": True}

    def test_bool_false(self) -> None:
        assert _otlp_value(False) == {"boolValue": False}

    def test_int(self) -> None:
        result = _otlp_value(42)
        assert result == {"intValue": "42"}

    def test_float(self) -> None:
        result = _otlp_value(3.14)
        assert result == {"doubleValue": 3.14}

    def test_string(self) -> None:
        assert _otlp_value("hello") == {"stringValue": "hello"}

    def test_other_type_coerced_to_string(self) -> None:
        result = _otlp_value([1, 2, 3])
        assert result == {"stringValue": "[1, 2, 3]"}

    def test_none_coerced_to_string(self) -> None:
        result = _otlp_value(None)
        assert result == {"stringValue": "None"}

    def test_bool_wins_over_int(self) -> None:
        # bool is subclass of int — must be checked first.
        result = _otlp_value(True)
        assert "boolValue" in result
        assert "intValue" not in result


# ---------------------------------------------------------------------------
# _kv
# ---------------------------------------------------------------------------


class TestKv:
    def test_string_value(self) -> None:
        result = _kv("service.name", "my-service")
        assert result == {"key": "service.name", "value": {"stringValue": "my-service"}}

    def test_int_value(self) -> None:
        result = _kv("count", 7)
        assert result["value"] == {"intValue": "7"}


# ---------------------------------------------------------------------------
# _ts_to_unix_nano
# ---------------------------------------------------------------------------


class TestTsToUnixNano:
    def test_unix_epoch_z(self) -> None:
        assert _ts_to_unix_nano("1970-01-01T00:00:00.000000Z") == 0

    def test_unix_epoch_offset(self) -> None:
        assert _ts_to_unix_nano("1970-01-01T00:00:00.000000+00:00") == 0

    def test_one_second(self) -> None:
        ns = _ts_to_unix_nano("1970-01-01T00:00:01.000000Z")
        assert ns == 1_000_000_000

    def test_microsecond_precision(self) -> None:
        ns = _ts_to_unix_nano("1970-01-01T00:00:00.000001Z")
        assert ns == 1_000  # 1µs = 1000ns

    def test_known_timestamp(self) -> None:
        # 2024-01-01T00:00:00Z
        ns = _ts_to_unix_nano("2024-01-01T00:00:00.000000Z")
        assert ns > 0
        # Approximate: 2024 is ~54 years after epoch.
        # 54 * 365.25 * 86400 * 1e9 ≈ 1.704e18 ns
        assert 1_700_000_000_000_000_000 < ns < 1_750_000_000_000_000_000

    def test_z_same_as_offset(self) -> None:
        ts = "2024-06-15T10:30:00.500000"
        ns_z = _ts_to_unix_nano(ts + "Z")
        ns_off = _ts_to_unix_nano(ts + "+00:00")
        assert ns_z == ns_off

    def test_naive_datetime_treated_as_utc(self) -> None:
        # A timestamp with no timezone info should be treated as UTC.
        ns = _ts_to_unix_nano("1970-01-01T00:00:01.000000")
        assert ns == 1_000_000_000


# ---------------------------------------------------------------------------
# _derive_span_id
# ---------------------------------------------------------------------------


class TestDeriveSpanId:
    def test_is_16_chars(self) -> None:
        event = _make_event()
        result = _derive_span_id(event.event_id)
        assert len(result) == 16

    def test_is_hex(self) -> None:
        event = _make_event()
        result = _derive_span_id(event.event_id)
        int(result, 16)  # raises ValueError if not valid hex

    def test_deterministic(self) -> None:
        event = _make_event()
        assert _derive_span_id(event.event_id) == _derive_span_id(event.event_id)

    def test_different_event_ids_differ(self) -> None:
        e1 = _make_event()
        e2 = _make_event()
        # ULIDs are unique by design; derived span IDs almost certainly differ.
        assert _derive_span_id(e1.event_id) != _derive_span_id(e2.event_id)


# ---------------------------------------------------------------------------
# _flatten_payload
# ---------------------------------------------------------------------------


class TestFlattenPayload:
    def test_flat_dict(self) -> None:
        result = _flatten_payload({"status": "ok"})
        assert {"key": "llm.payload.status", "value": {"stringValue": "ok"}} in result

    def test_nested_dict(self) -> None:
        result = _flatten_payload({"model": {"name": "gpt-4", "version": "0613"}})
        keys = [item["key"] for item in result]
        assert "llm.payload.model.name" in keys
        assert "llm.payload.model.version" in keys

    def test_custom_prefix(self) -> None:
        result = _flatten_payload({"x": 1}, prefix="custom")
        assert result[0]["key"] == "custom.x"

    def test_empty_dict(self) -> None:
        assert _flatten_payload({}) == []

    def test_deeply_nested(self) -> None:
        payload = {"a": {"b": {"c": "deep"}}}
        result = _flatten_payload(payload)
        assert any(item["key"] == "llm.payload.a.b.c" for item in result)


# ---------------------------------------------------------------------------
# _event_to_attributes
# ---------------------------------------------------------------------------


class TestEventToAttributes:
    def _attr_value(self, attrs: list[dict], key: str) -> Any:
        for item in attrs:
            if item["key"] == key:
                v = item["value"]
                return next(iter(v.values()))
        raise KeyError(key)

    def test_required_envelope_fields_present(self) -> None:
        event = _make_event()
        attrs = _event_to_attributes(event)
        keys = {a["key"] for a in attrs}
        assert "llm.schema_version" in keys
        assert "llm.event_id" in keys
        assert "llm.event_type" in keys
        assert "llm.source" in keys

    def test_org_id_included_when_set(self) -> None:
        event = _make_event(org_id="acme")
        attrs = _event_to_attributes(event)
        assert self._attr_value(attrs, "llm.org_id") == "acme"

    def test_team_id_included_when_set(self) -> None:
        event = _make_event(team_id="backend")
        attrs = _event_to_attributes(event)
        assert self._attr_value(attrs, "llm.team_id") == "backend"

    def test_actor_id_included_when_set(self) -> None:
        event = _make_event(actor_id="user-123")
        attrs = _event_to_attributes(event)
        assert self._attr_value(attrs, "llm.actor_id") == "user-123"

    def test_session_id_included_when_set(self) -> None:
        event = _make_event(session_id="sess-abc")
        attrs = _event_to_attributes(event)
        assert self._attr_value(attrs, "llm.session_id") == "sess-abc"

    def test_optional_identity_fields_absent_when_not_set(self) -> None:
        event = _make_event()
        attrs = _event_to_attributes(event)
        keys = {a["key"] for a in attrs}
        assert "llm.org_id" not in keys
        assert "llm.team_id" not in keys
        assert "llm.actor_id" not in keys
        assert "llm.session_id" not in keys

    def test_tags_mapped_with_prefix(self) -> None:
        event = _make_event(tags=Tags(env="prod", region="eu"))
        attrs = _event_to_attributes(event)
        keys = {a["key"] for a in attrs}
        assert "llm.tag.env" in keys
        assert "llm.tag.region" in keys

    def test_checksum_included_when_set(self) -> None:
        event = _make_event(checksum="sha256:abc123")
        attrs = _event_to_attributes(event)
        assert self._attr_value(attrs, "llm.checksum") == "sha256:abc123"

    def test_signature_included_when_set(self) -> None:
        event = _make_event(signature="hmac-sha256:deadbeef")
        attrs = _event_to_attributes(event)
        assert self._attr_value(attrs, "llm.signature") == "hmac-sha256:deadbeef"

    def test_prev_id_included_when_set(self) -> None:
        prev = _make_event()
        event = _make_event(prev_id=prev.event_id)
        attrs = _event_to_attributes(event)
        assert self._attr_value(attrs, "llm.prev_id") == prev.event_id

    def test_payload_fields_flattened(self) -> None:
        event = _make_event(payload={"status": "ok", "tokens": 10})
        attrs = _event_to_attributes(event)
        keys = {a["key"] for a in attrs}
        assert "llm.payload.status" in keys
        assert "llm.payload.tokens" in keys


# ---------------------------------------------------------------------------
# ResourceAttributes
# ---------------------------------------------------------------------------


class TestResourceAttributes:
    def test_default_deployment_environment(self) -> None:
        ra = ResourceAttributes(service_name="svc")
        assert ra.deployment_environment == "production"

    def test_to_otlp_contains_service_name(self) -> None:
        ra = ResourceAttributes(service_name="svc")
        attrs = ra.to_otlp()
        keys = {a["key"] for a in attrs}
        assert "service.name" in keys

    def test_to_otlp_contains_environment(self) -> None:
        ra = ResourceAttributes(service_name="svc", deployment_environment="staging")
        attrs = ra.to_otlp()
        keys = {a["key"] for a in attrs}
        assert "deployment.environment.name" in keys

    def test_extra_attrs_included(self) -> None:
        ra = ResourceAttributes(
            service_name="svc",
            extra={"k8s.namespace": "default", "cloud.provider": "aws"},
        )
        attrs = ra.to_otlp()
        keys = {a["key"] for a in attrs}
        assert "k8s.namespace" in keys
        assert "cloud.provider" in keys

    def test_no_extra_attrs(self) -> None:
        ra = ResourceAttributes(service_name="svc")
        attrs = ra.to_otlp()
        # Only service.name and deployment.environment.name
        assert len(attrs) == 2

    def test_extra_empty_dict(self) -> None:
        ra = ResourceAttributes(service_name="svc", extra={})
        assert len(ra.to_otlp()) == 2


# ---------------------------------------------------------------------------
# OTLPExporter.__init__ validation
# ---------------------------------------------------------------------------


class TestOTLPExporterInit:
    def test_empty_endpoint_raises(self) -> None:
        with pytest.raises(ValueError, match="endpoint"):
            OTLPExporter("")

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            OTLPExporter("http://localhost", timeout=0.0)  # NOSONAR

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            OTLPExporter("http://localhost", timeout=-1.0)  # NOSONAR

    def test_zero_batch_size_raises(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            OTLPExporter("http://localhost", batch_size=0)  # NOSONAR

    def test_defaults_applied(self) -> None:
        exp = OTLPExporter("http://localhost")  # NOSONAR
        assert exp._timeout == pytest.approx(5.0)
        assert exp._batch_size == 500

    def test_invalid_scheme_url_raises(self) -> None:
        with pytest.raises(ValueError, match="endpoint"):
            OTLPExporter("ftp://invalid-scheme.example.com")  # NOSONAR

    def test_custom_headers_copied(self) -> None:
        headers = {"Authorization": "Bearer token"}
        exp = OTLPExporter("http://localhost", headers=headers)  # NOSONAR
        assert exp._headers == headers
        # Modifying original should not affect exporter's copy.
        headers["X-Extra"] = "extra"
        assert "X-Extra" not in exp._headers

    def test_custom_resource_attrs(self) -> None:
        ra = ResourceAttributes(service_name="custom-svc")
        exp = OTLPExporter("http://localhost", resource_attrs=ra)  # NOSONAR
        assert exp._resource_attrs is ra

    def test_default_resource_attrs_set(self) -> None:
        exp = OTLPExporter("http://localhost")  # NOSONAR
        assert exp._resource_attrs.service_name == "agentobs"


# ---------------------------------------------------------------------------
# OTLPExporter.to_otlp_span
# ---------------------------------------------------------------------------


class TestToOtlpSpan:
    def test_basic_structure(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32)
        span = exp.to_otlp_span(event)
        assert span["traceId"] == "a" * 32
        assert span["name"] == event.event_type
        assert "startTimeUnixNano" in span
        assert "endTimeUnixNano" in span
        assert span["startTimeUnixNano"] == span["endTimeUnixNano"]
        assert "attributes" in span
        assert span["status"] == {"code": 1}

    def test_uses_event_span_id_when_present(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32, span_id="b" * 16)
        span = exp.to_otlp_span(event)
        assert span["spanId"] == "b" * 16

    def test_derives_span_id_when_absent(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32)
        span = exp.to_otlp_span(event)
        assert span["spanId"] == _derive_span_id(event.event_id)

    def test_fallback_trace_id_when_event_has_none(self) -> None:
        # When called explicitly even for an event without trace_id
        exp = _make_exporter()
        event = _make_event()  # no trace_id
        span = exp.to_otlp_span(event)
        assert span["traceId"] == "0" * 32

    def test_parent_span_id_included_when_set(self) -> None:
        exp = _make_exporter()
        event = _make_event(
            trace_id="a" * 32,
            span_id="b" * 16,
            parent_span_id="c" * 16,
        )
        span = exp.to_otlp_span(event)
        assert span["parentSpanId"] == "c" * 16

    def test_parent_span_id_absent_when_not_set(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32)
        span = exp.to_otlp_span(event)
        assert "parentSpanId" not in span

    def test_attributes_is_list(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32)
        span = exp.to_otlp_span(event)
        assert isinstance(span["attributes"], list)


# ---------------------------------------------------------------------------
# OTLPExporter.to_otlp_log
# ---------------------------------------------------------------------------


class TestToOtlpLog:
    def test_basic_structure(self) -> None:
        exp = _make_exporter()
        event = _make_event()
        log = exp.to_otlp_log(event)
        assert log["severityNumber"] == 9
        assert log["severityText"] == "INFO"
        assert log["body"] == {"stringValue": event.event_type}
        assert "timeUnixNano" in log
        assert "observedTimeUnixNano" in log
        assert "attributes" in log

    def test_trace_id_included_when_set(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32)
        log = exp.to_otlp_log(event)
        assert log["traceId"] == "a" * 32

    def test_span_id_included_when_set(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32, span_id="b" * 16)
        log = exp.to_otlp_log(event)
        assert log["spanId"] == "b" * 16

    def test_trace_id_absent_when_not_set(self) -> None:
        exp = _make_exporter()
        event = _make_event()
        log = exp.to_otlp_log(event)
        assert "traceId" not in log

    def test_span_id_absent_when_not_set(self) -> None:
        exp = _make_exporter()
        event = _make_event()
        log = exp.to_otlp_log(event)
        assert "spanId" not in log


# ---------------------------------------------------------------------------
# OTLPExporter._wrap_spans / _wrap_logs
# ---------------------------------------------------------------------------


class TestWrapHelpers:
    def test_wrap_spans_structure(self) -> None:
        exp = _make_exporter()
        result = exp._wrap_spans([{"name": "test"}])
        assert "resourceSpans" in result
        rs = result["resourceSpans"][0]
        assert "resource" in rs
        scope_spans = rs["scopeSpans"][0]
        assert scope_spans["spans"] == [{"name": "test"}]

    def test_wrap_logs_structure(self) -> None:
        exp = _make_exporter()
        result = exp._wrap_logs([{"body": "msg"}])
        assert "resourceLogs" in result
        rl = result["resourceLogs"][0]
        scope_logs = rl["scopeLogs"][0]
        assert scope_logs["logRecords"] == [{"body": "msg"}]

    def test_wrap_spans_includes_scope_name(self) -> None:
        exp = _make_exporter()
        result = exp._wrap_spans([])
        scope = result["resourceSpans"][0]["scopeSpans"][0]["scope"]
        assert scope["name"] == "agentobs"

    def test_wrap_logs_includes_scope_name(self) -> None:
        exp = _make_exporter()
        result = exp._wrap_logs([])
        scope = result["resourceLogs"][0]["scopeLogs"][0]["scope"]
        assert scope["name"] == "agentobs"

    def test_wrap_spans_resource_uses_configured_attrs(self) -> None:
        ra = ResourceAttributes(service_name="my-svc")
        exp = OTLPExporter("http://localhost", resource_attrs=ra)  # NOSONAR
        result = exp._wrap_spans([])
        resource_attrs = result["resourceSpans"][0]["resource"]["attributes"]
        keys = {a["key"] for a in resource_attrs}
        assert "service.name" in keys


# ---------------------------------------------------------------------------
# OTLPExporter._send
# ---------------------------------------------------------------------------


class TestSend:
    def test_send_success(self) -> None:
        exp = _make_exporter()
        ctx = _mock_urlopen_success()
        with patch("urllib.request.urlopen", return_value=ctx):
            asyncio.run(exp._send({"test": "payload"}))

    def test_send_http_error_raises_export_error(self) -> None:
        exp = _make_exporter()
        http_err = urllib.error.HTTPError(
            url="http://localhost",  # NOSONAR
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,  # type: ignore[arg-type]
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(ExportError) as exc_info:
                asyncio.run(exp._send({"test": "payload"}))
            assert exc_info.value.backend == "otlp"
            assert "500" in exc_info.value.reason

    def test_send_os_error_raises_export_error(self) -> None:
        exp = _make_exporter()
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            with pytest.raises(ExportError) as exc_info:
                asyncio.run(exp._send({"test": "payload"}))
            assert exc_info.value.backend == "otlp"
            assert "connection refused" in exc_info.value.reason

    def test_send_posts_json_content_type(self) -> None:
        exp = _make_exporter()
        captured: list = []

        def _fake_urlopen(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            asyncio.run(exp._send({"key": "val"}))

        req = captured[0]
        assert req.get_header("Content-type") == "application/json"

    def test_send_includes_custom_headers(self) -> None:
        exp = OTLPExporter(
            "http://localhost",  # NOSONAR
            headers={"X-Api-Key": "secret-key"},
        )
        captured: list = []

        def _fake_urlopen(req, timeout=None):
            captured.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            asyncio.run(exp._send({"k": "v"}))

        req = captured[0]
        # urllib capitalises first letter of each header component.
        assert req.get_header("X-api-key") == "secret-key"


# ---------------------------------------------------------------------------
# OTLPExporter.export (integration: span path)
# ---------------------------------------------------------------------------


class TestExportSpanPath:
    def test_export_with_trace_id_returns_span(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32)
        ctx = _mock_urlopen_success()
        with patch("urllib.request.urlopen", return_value=ctx):
            record = asyncio.run(exp.export(event))
        assert "traceId" in record
        assert record["traceId"] == "a" * 32

    def test_export_without_trace_id_returns_log(self) -> None:
        exp = _make_exporter()
        event = _make_event()  # no trace_id
        ctx = _mock_urlopen_success()
        with patch("urllib.request.urlopen", return_value=ctx):
            record = asyncio.run(exp.export(event))
        assert "body" in record  # log record marker
        assert "traceId" not in record


# ---------------------------------------------------------------------------
# OTLPExporter.export_batch
# ---------------------------------------------------------------------------


class TestExportBatch:
    def test_batch_all_spans(self) -> None:
        exp = _make_exporter()
        events = [_make_event(trace_id="a" * 32) for _ in range(5)]
        calls: list = []

        def _fake(req, timeout=None):
            calls.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            records = asyncio.run(exp.export_batch(events))

        assert len(records) == 5
        # All spans → one POST
        assert len(calls) == 1

    def test_batch_all_logs(self) -> None:
        exp = _make_exporter()
        events = [_make_event() for _ in range(5)]
        calls: list = []

        def _fake(req, timeout=None):
            calls.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            records = asyncio.run(exp.export_batch(events))

        assert len(records) == 5
        assert len(calls) == 1

    def test_batch_mixed_sends_two_requests(self) -> None:
        exp = _make_exporter()
        events = [
            _make_event(trace_id="a" * 32),  # span
            _make_event(),  # log
        ]
        calls: list = []

        def _fake(req, timeout=None):
            calls.append(req)
            return _mock_urlopen_success()

        with patch("urllib.request.urlopen", side_effect=_fake):
            records = asyncio.run(exp.export_batch(events))

        assert len(records) == 2
        assert len(calls) == 2

    def test_batch_empty_no_requests(self) -> None:
        exp = _make_exporter()
        calls: list = []

        with patch("urllib.request.urlopen", side_effect=calls.append):
            records = asyncio.run(exp.export_batch([]))

        assert records == []
        assert len(calls) == 0


# ---------------------------------------------------------------------------
# OTLPExporter.__repr__
# ---------------------------------------------------------------------------


class TestOTLPRepr:
    def test_repr_contains_endpoint(self) -> None:
        exp = OTLPExporter("http://localhost:4318")  # NOSONAR
        assert "http://localhost:4318" in repr(exp)  # NOSONAR

    def test_repr_contains_batch_size(self) -> None:
        exp = OTLPExporter("http://localhost", batch_size=250)  # NOSONAR
        assert "250" in repr(exp)

    def test_repr_does_not_leak_headers(self) -> None:
        exp = OTLPExporter("http://localhost", headers={"Authorization": "Bearer top-secret"})  # NOSONAR
        assert "top-secret" not in repr(exp)


# ---------------------------------------------------------------------------
# Performance: serialisation of 500 events in < 200 ms
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _gen_ai_attributes
# ---------------------------------------------------------------------------


class TestGenAiAttributes:
    """Tests for the gen_ai.* semantic convention attribute mapper."""

    def _event(self, payload: dict) -> Event:
        return Event(
            event_type="llm.trace.span.completed",
            source="test@1.0.0",
            payload=payload,
        )

    def test_span_name_maps_to_gen_ai_operation_name(self) -> None:
        event = self._event({"span_name": "run_agent", "status": "ok"})
        keys = {kv["key"] for kv in _gen_ai_attributes(event)}
        assert "gen_ai.operation.name" in keys

    def test_model_provider_maps_to_gen_ai_system(self) -> None:
        event = self._event({"model": {"name": "gpt-4o", "provider": "openai"}, "status": "ok"})
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.system"]["stringValue"] == "openai"

    def test_model_name_maps_to_gen_ai_request_model(self) -> None:
        event = self._event({"model": {"name": "gpt-4o", "provider": "openai"}, "status": "ok"})
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.request.model"]["stringValue"] == "gpt-4o"

    def test_model_version_maps_to_gen_ai_request_model_version(self) -> None:
        event = self._event(
            {"model": {"name": "gpt-4o", "provider": "openai", "version": "2024-05-13"}, "status": "ok"}  # noqa: E501
        )
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.request.model_version"]["stringValue"] == "2024-05-13"

    def test_token_usage_prompt_maps_to_input_tokens(self) -> None:
        event = self._event(
            {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
             "status": "ok"}
        )
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.usage.input_tokens"]["intValue"] == "10"

    def test_token_usage_completion_maps_to_output_tokens(self) -> None:
        event = self._event(
            {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
             "status": "ok"}
        )
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.usage.output_tokens"]["intValue"] == "5"

    def test_status_error_maps_to_finish_reason_error(self) -> None:
        event = self._event({"status": "error", "error": "upstream failure"})
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.response.finish_reasons"]["stringValue"] == "error"

    def test_status_timeout_maps_to_finish_reason_timeout(self) -> None:
        event = self._event({"status": "timeout"})
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.response.finish_reasons"]["stringValue"] == "timeout"

    def test_status_ok_maps_to_finish_reason_stop(self) -> None:
        event = self._event({"status": "ok"})
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.response.finish_reasons"]["stringValue"] == "stop"

    def test_error_field_without_status_maps_to_finish_reason_error(self) -> None:
        event = self._event({"error": "some error", "dummy": "val"})
        attrs = {kv["key"]: kv["value"] for kv in _gen_ai_attributes(event)}
        assert attrs["gen_ai.response.finish_reasons"]["stringValue"] == "error"

    def test_no_model_no_tokens_empty_payload_returns_empty(self) -> None:
        event = self._event({"dummy": "value"})
        assert _gen_ai_attributes(event) == []


# ---------------------------------------------------------------------------
# _map_span_status
# ---------------------------------------------------------------------------


class TestMapSpanStatus:
    """Tests for the OTLP SpanStatus mapper."""

    def _event(self, payload: dict) -> Event:
        return Event(
            event_type="llm.trace.span.completed",
            source="test@1.0.0",
            payload=payload,
        )

    def test_status_ok_returns_code_1(self) -> None:
        assert _map_span_status(self._event({"status": "ok"})) == {"code": 1}

    def test_no_status_defaults_to_ok(self) -> None:
        assert _map_span_status(self._event({"value": 1})) == {"code": 1}

    def test_status_error_with_message_returns_code_2_and_message(self) -> None:
        result = _map_span_status(self._event({"status": "error", "error": "bad request"}))
        assert result["code"] == 2
        assert result["message"] == "bad request"

    def test_status_error_without_message_returns_code_2_no_message(self) -> None:
        result = _map_span_status(self._event({"status": "error"}))
        assert result["code"] == 2
        assert "message" not in result

    def test_status_timeout_returns_code_2_and_default_message(self) -> None:
        result = _map_span_status(self._event({"status": "timeout"}))
        assert result["code"] == 2
        assert "timed out" in result["message"].lower()


# ---------------------------------------------------------------------------
# _compute_end_nano
# ---------------------------------------------------------------------------


class TestComputeEndNano:
    """Tests for the endTimeUnixNano computation helper."""

    def _event(self, payload: dict) -> Event:
        return Event(
            event_type="llm.trace.span.completed",
            source="test@1.0.0",
            payload=payload,
        )

    def test_duration_ms_adds_to_start_nano(self) -> None:
        start = 1_000_000_000_000
        event = self._event({"duration_ms": 100.0, "x": 1})
        end = _compute_end_nano(start, event)
        assert end == start + 100_000_000

    def test_no_duration_ms_returns_start_nano(self) -> None:
        start = 1_000_000_000_000
        event = self._event({"x": 1})
        assert _compute_end_nano(start, event) == start


# ---------------------------------------------------------------------------
# to_otlp_span — OTel wire-format fields
# ---------------------------------------------------------------------------


class TestToOtlpSpanOtelFields:
    """Verify the new OTel wire-format fields on generated spans."""

    def test_span_has_kind_client(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32)
        span = exp.to_otlp_span(event)
        assert span["kind"] == 3  # SPAN_KIND_CLIENT

    def test_span_has_trace_flags_sampled(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32)
        span = exp.to_otlp_span(event)
        assert span["traceFlags"] == 1

    def test_end_time_equals_start_when_no_duration(self) -> None:
        exp = _make_exporter()
        event = _make_event(trace_id="a" * 32, payload={"status": "ok"})
        span = exp.to_otlp_span(event)
        assert span["endTimeUnixNano"] == span["startTimeUnixNano"]

    def test_end_time_greater_than_start_when_duration_present(self) -> None:
        exp = _make_exporter()
        event = _make_event(
            trace_id="a" * 32,
            payload={"status": "ok", "duration_ms": 250.0},
        )
        span = exp.to_otlp_span(event)
        assert int(span["endTimeUnixNano"]) > int(span["startTimeUnixNano"])

    def test_error_payload_produces_status_code_2(self) -> None:
        exp = _make_exporter()
        event = _make_event(
            trace_id="a" * 32,
            payload={"status": "error", "error": "upstream failed"},
        )
        span = exp.to_otlp_span(event)
        assert span["status"]["code"] == 2
        assert span["status"]["message"] == "upstream failed"

    def test_gen_ai_attributes_present_in_span(self) -> None:
        exp = _make_exporter()
        event = _make_event(
            trace_id="a" * 32,
            payload={"status": "ok", "model": {"name": "gpt-4o", "provider": "openai"}},
        )
        span = exp.to_otlp_span(event)
        attr_keys = {a["key"] for a in span["attributes"]}
        assert "gen_ai.system" in attr_keys
        assert "gen_ai.request.model" in attr_keys


# ---------------------------------------------------------------------------
# make_traceparent
# ---------------------------------------------------------------------------


class TestMakeTraceparent:
    """Tests for the W3C traceparent header builder."""

    _TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
    _SPAN = "00f067aa0ba902b7"

    def test_produces_correct_format(self) -> None:
        result = make_traceparent(self._TRACE, self._SPAN)
        assert result == f"00-{self._TRACE}-{self._SPAN}-01"

    def test_sampled_false_uses_00_flag(self) -> None:
        result = make_traceparent(self._TRACE, self._SPAN, sampled=False)
        assert result.endswith("-00")

    def test_sampled_true_uses_01_flag(self) -> None:
        result = make_traceparent(self._TRACE, self._SPAN, sampled=True)
        assert result.endswith("-01")

    def test_invalid_trace_id_raises(self) -> None:
        with pytest.raises(ValueError, match="trace_id"):
            make_traceparent("short", self._SPAN)

    def test_invalid_span_id_raises(self) -> None:
        with pytest.raises(ValueError, match="span_id"):
            make_traceparent(self._TRACE, "tooshort")

    def test_uppercase_trace_id_raises(self) -> None:
        with pytest.raises(ValueError, match="trace_id"):
            make_traceparent(self._TRACE.upper(), self._SPAN)


# ---------------------------------------------------------------------------
# extract_trace_context
# ---------------------------------------------------------------------------


class TestExtractTraceContext:
    """Tests for the W3C traceparent / tracestate header parser."""

    _TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
    _SPAN = "00f067aa0ba902b7"

    def _make_headers(self, sampled: bool = True, **extra: str) -> dict:
        flags = "01" if sampled else "00"
        return {"traceparent": f"00-{self._TRACE}-{self._SPAN}-{flags}", **extra}

    def test_valid_traceparent_returns_context(self) -> None:
        ctx = extract_trace_context(self._make_headers())
        assert ctx is not None
        assert ctx["trace_id"] == self._TRACE
        assert ctx["span_id"] == self._SPAN
        assert ctx["sampled"] is True

    def test_not_sampled_flag_returns_false(self) -> None:
        ctx = extract_trace_context(self._make_headers(sampled=False))
        assert ctx is not None
        assert ctx["sampled"] is False

    def test_tracestate_included_when_present(self) -> None:
        headers = {**self._make_headers(), "tracestate": "vendor=abc"}
        ctx = extract_trace_context(headers)
        assert ctx is not None
        assert ctx["tracestate"] == "vendor=abc"

    def test_case_insensitive_header_key(self) -> None:
        headers = {"Traceparent": f"00-{self._TRACE}-{self._SPAN}-01"}
        ctx = extract_trace_context(headers)
        assert ctx is not None
        assert ctx["trace_id"] == self._TRACE

    def test_missing_traceparent_returns_none(self) -> None:
        assert extract_trace_context({}) is None

    def test_malformed_too_few_parts_returns_none(self) -> None:
        assert extract_trace_context({"traceparent": "00-abc"}) is None

    def test_non_zero_zero_version_returns_none(self) -> None:
        assert extract_trace_context(
            {"traceparent": f"01-{self._TRACE}-{self._SPAN}-01"}
        ) is None

    def test_wrong_trace_id_length_returns_none(self) -> None:
        assert extract_trace_context(
            {"traceparent": f"00-{'a' * 16}-{self._SPAN}-01"}
        ) is None

    def test_wrong_span_id_length_returns_none(self) -> None:
        assert extract_trace_context(
            {"traceparent": f"00-{self._TRACE}-{'a' * 8}-01"}
        ) is None

    def test_invalid_trace_id_chars_returns_none(self) -> None:
        assert extract_trace_context(
            {"traceparent": f"00-{'x' * 32}-{self._SPAN}-01"}
        ) is None

    def test_invalid_span_id_chars_returns_none(self) -> None:
        assert extract_trace_context(
            {"traceparent": f"00-{self._TRACE}-{'z' * 16}-01"}
        ) is None


# ---------------------------------------------------------------------------
# Performance: serialisation of 500 events in < 200 ms
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestPerformance:
    def test_export_batch_500_events_serialisation_under_200ms(self) -> None:
        """Serialisation (to_otlp_span + to_otlp_log) of 500 events must be < 200ms."""
        exp = _make_exporter()
        # Mix of spans and logs
        events = [
            _make_event(trace_id="a" * 32) if i % 2 == 0 else _make_event()
            for i in range(500)
        ]

        start = time.perf_counter()
        for event in events:
            if event.trace_id:
                exp.to_otlp_span(event)
            else:
                exp.to_otlp_log(event)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 200, (
            f"Serialisation of 500 events took {elapsed_ms:.1f} ms (limit: 200 ms)"
        )
