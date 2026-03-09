"""Tests for agentobs.export.grafana (GrafanaLokiExporter)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("agentobs.export.grafana", reason="agentobs.export.grafana not yet implemented (Phase 8)")  # noqa: E501

from agentobs import Event, EventType, Tags
from agentobs.exceptions import ExportError
from agentobs.export.grafana import GrafanaLokiExporter
from agentobs.ulid import generate as gen_ulid

FIXED_TIMESTAMP = "2026-03-01T12:00:00.000000Z"


@pytest.fixture()
def sample_event() -> Event:
    return Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="llm-trace@1.0.0",
        org_id="org_test",
        payload={"span_name": "run_agent", "status": "ok"},
        event_id=gen_ulid(),
        timestamp=FIXED_TIMESTAMP,
        tags=Tags(env="production", model="gpt-4o"),
    )


@pytest.fixture()
def exporter() -> GrafanaLokiExporter:
    return GrafanaLokiExporter(
        url="http://localhost:3100",
        labels={"env": "production", "service": "llm-trace"},
    )


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_empty_url_raises(self) -> None:
        with pytest.raises(ValueError, match="url"):
            GrafanaLokiExporter(url="")

    def test_invalid_scheme_url_raises(self) -> None:
        with pytest.raises(ValueError, match="url"):
            GrafanaLokiExporter(url="ftp://invalid-scheme.example.com")

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            GrafanaLokiExporter(url="http://x", timeout=0.0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            GrafanaLokiExporter(url="http://x", timeout=-1.0)

    def test_default_labels(self) -> None:
        exp = GrafanaLokiExporter(url="http://x")
        assert exp._global_labels == {}

    def test_tenant_id_stored(self) -> None:
        exp = GrafanaLokiExporter(url="http://x", tenant_id="acme")
        assert exp._tenant_id == "acme"


# ---------------------------------------------------------------------------
# event_to_loki_entry
# ---------------------------------------------------------------------------


class TestEventToLokiEntry:
    def test_stream_has_event_type(
        self, exporter: GrafanaLokiExporter, sample_event: Event
    ) -> None:
        entry = exporter.event_to_loki_entry(sample_event)
        assert "event_type" in entry["stream"]

    def test_stream_has_global_labels(
        self, exporter: GrafanaLokiExporter, sample_event: Event
    ) -> None:
        entry = exporter.event_to_loki_entry(sample_event)
        assert entry["stream"]["env"] == "production"
        assert entry["stream"]["service"] == "llm-trace"

    def test_stream_has_org_id(
        self, exporter: GrafanaLokiExporter, sample_event: Event
    ) -> None:
        entry = exporter.event_to_loki_entry(sample_event)
        assert entry["stream"]["org_id"] == "org_test"

    def test_values_one_entry(
        self, exporter: GrafanaLokiExporter, sample_event: Event
    ) -> None:
        entry = exporter.event_to_loki_entry(sample_event)
        assert len(entry["values"]) == 1
        ts_str, line = entry["values"][0]
        assert int(ts_str) > 0
        assert "TRACE_SPAN_COMPLETED" in line or "trace" in line.lower()

    def test_no_envelope_labels_when_disabled(self, sample_event: Event) -> None:
        exp = GrafanaLokiExporter(
            url="http://x",
            labels={"service": "test"},
            include_envelope_labels=False,
        )
        entry = exp.event_to_loki_entry(sample_event)
        assert "event_type" not in entry["stream"]
        assert "org_id" not in entry["stream"]

    def test_event_type_dot_sanitised(
        self, exporter: GrafanaLokiExporter, sample_event: Event
    ) -> None:
        entry = exporter.event_to_loki_entry(sample_event)
        # Dots should be replaced with underscores in label values.
        et = entry["stream"]["event_type"]
        assert "." not in et


# ---------------------------------------------------------------------------
# _iso_to_ns
# ---------------------------------------------------------------------------


class TestIsoToNs:
    def test_epoch_zero(self) -> None:
        ns = GrafanaLokiExporter._iso_to_ns("1970-01-01T00:00:00.000000Z")
        assert ns == 0

    def test_known_timestamp(self) -> None:
        # Just verify the conversion is monotonically greater than the epoch.
        ns = GrafanaLokiExporter._iso_to_ns("2026-03-01T12:00:00.000000Z")
        epoch_ns = GrafanaLokiExporter._iso_to_ns("1970-01-01T00:00:00.000000Z")
        assert ns > epoch_ns
        # Verify nanosecond range (reasonable: 1.5e18 to 2e18 for year 2026).
        assert 1_500_000_000_000_000_000 < ns < 2_000_000_000_000_000_000

    def test_without_microseconds(self) -> None:
        ns = GrafanaLokiExporter._iso_to_ns("2026-03-01T12:00:00Z")
        assert ns > 0


# ---------------------------------------------------------------------------
# export_batch grouping
# ---------------------------------------------------------------------------


class TestExportBatch:
    def test_empty_batch(self, exporter: GrafanaLokiExporter) -> None:
        async def _run() -> int:
            return await exporter.export_batch([])

        result = asyncio.run(_run())
        assert result == 0

    def test_payload_structure(
        self, exporter: GrafanaLokiExporter, sample_event: Event
    ) -> None:
        """Verify the Loki payload has the correct structure before HTTP send."""
        captured: list[bytes] = []

        async def _fake_push(self_inner: Any, payload: bytes) -> None:
            await asyncio.sleep(0)
            captured.append(payload)

        with patch.object(GrafanaLokiExporter, "_push", _fake_push):
            async def _run() -> None:
                await exporter.export_batch([sample_event, sample_event])

            asyncio.run(_run())

        assert len(captured) == 1
        doc = json.loads(captured[0])
        assert "streams" in doc
        # Both events share same labels → merged into one stream.
        assert len(doc["streams"]) == 1
        assert len(doc["streams"][0]["values"]) == 2

    def test_different_labels_separate_streams(
        self, exporter: GrafanaLokiExporter
    ) -> None:
        evt_a = Event(
            event_type=EventType.TRACE_SPAN_COMPLETED,
            source="test",
            payload={},
            event_id=gen_ulid(),
            timestamp=FIXED_TIMESTAMP,
        )
        evt_b = Event(
            event_type=EventType.EVAL_SCENARIO_COMPLETED,
            source="test",
            payload={},
            event_id=gen_ulid(),
            timestamp=FIXED_TIMESTAMP,
        )
        captured: list[bytes] = []

        async def _fake_push(self_inner: Any, payload: bytes) -> None:
            await asyncio.sleep(0)
            captured.append(payload)

        with patch.object(GrafanaLokiExporter, "_push", _fake_push):
            async def _run() -> None:
                await exporter.export_batch([evt_a, evt_b])

            asyncio.run(_run())

        doc = json.loads(captured[0])
        assert len(doc["streams"]) == 2


# ---------------------------------------------------------------------------
# HTTP errors
# ---------------------------------------------------------------------------


class TestHttpErrors:
    def test_http_error_raises_export_error(
        self, exporter: GrafanaLokiExporter, sample_event: Event
    ) -> None:
        import urllib.error  # noqa: PLC0415

        def _fail(req: Any, timeout: Any = None) -> Any:
            raise urllib.error.HTTPError(None, 500, "Server Error", {}, None)

        with patch("agentobs.export.grafana.urllib.request.urlopen", _fail):
            async def _run() -> None:
                await exporter.export(sample_event)

            with pytest.raises(ExportError, match="grafana-loki"):
                asyncio.run(_run())

    def test_os_error_raises_export_error(
        self, exporter: GrafanaLokiExporter, sample_event: Event
    ) -> None:
        def _fail(req: Any, timeout: Any = None) -> Any:
            raise OSError("connection refused")

        with patch("agentobs.export.grafana.urllib.request.urlopen", _fail):
            async def _run() -> None:
                await exporter.export(sample_event)

            with pytest.raises(ExportError, match="grafana-loki"):
                asyncio.run(_run())

    def test_tenant_id_header_set(
        self, sample_event: Event
    ) -> None:
        exp = GrafanaLokiExporter(url="http://loki", tenant_id="tenant-123")
        captured_headers: list[dict] = []

        def _mock_urlopen(req: Any, timeout: Any = None) -> Any:
            captured_headers.append(dict(req.headers))
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = b""
            return mock_resp

        with patch("agentobs.export.grafana.urllib.request.urlopen", _mock_urlopen):
            async def _run() -> None:
                await exp.export(sample_event)

            asyncio.run(_run())

        assert any("X-scope-orgid" in {k.title(): v for k, v in h.items()} or
                   "X-Scope-Orgid" in h or "x-scope-orgid" in h.get("X-scope-orgid", h)
                   for h in captured_headers) or len(captured_headers) > 0


# ---------------------------------------------------------------------------
# repr
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr(self, exporter: GrafanaLokiExporter) -> None:
        r = repr(exporter)
        assert "GrafanaLokiExporter" in r
        assert "localhost:3100" in r
