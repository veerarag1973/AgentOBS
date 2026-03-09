"""Tests for agentobs/export/otel_bridge.py — OTelBridgeExporter.

The opentelemetry-sdk is an optional dependency so all tests mock the
``opentelemetry`` package via ``sys.modules`` to avoid requiring it at
test time.

Coverage targets
----------------
* ``OTelBridgeExporter`` constructor (success and missing-dep failure).
* ``_build_otel_attributes`` — llm.* and gen_ai.* attribute mapping.
* ``_resolve_span_context`` — with and without trace_id.
* ``export`` / ``export_batch`` — successful paths.
* ``__repr__``.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Windows ProactorEventLoop may emit an unclosed-loop ResourceWarning after
# async tests complete.  Suppress it at the module level.
pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")

from agentobs.event import Event, Tags  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    span_id: str | None = None,
    payload: dict | None = None,
    tags: Tags | None = None,
) -> Event:
    return Event(
        event_type="llm.trace.span.completed",
        source="test@1.0.0",
        payload=payload or {"status": "ok", "span_name": "run"},
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        tags=tags,
    )


def _build_otel_mock() -> types.ModuleType:
    """Build a minimal ``opentelemetry`` mock namespace that satisfies the bridge."""
    otel = types.ModuleType("opentelemetry")

    # opentelemetry.trace
    trace_mod = types.ModuleType("opentelemetry.trace")

    class _SpanKind:
        CLIENT = 3
        INTERNAL = 0

    class _StatusCode:
        OK = "OK"
        ERROR = "ERROR"

    class _TraceFlags(int):
        SAMPLED = 0x01

    class _SpanContext:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class _NonRecordingSpan:
        def __init__(self, context: Any) -> None:
            self.context = context

    class _FakeSpan:
        def __init__(self) -> None:
            self.attributes: dict = {}
            self._status: Any = None
            self._ended = False

        def set_status(self, code: Any, message: str = "") -> None:
            self._status = (code, message)

        def end(self) -> None:
            self._ended = True

    class _FakeTracer:
        def __init__(self) -> None:
            self.started_spans: list = []

        def start_span(self, name: str, **kwargs: Any) -> _FakeSpan:
            span = _FakeSpan()
            self.started_spans.append((name, kwargs, span))
            return span

    _fake_tracer = _FakeTracer()

    def _get_tracer(name: str, version: str = "") -> _FakeTracer:
        return _fake_tracer

    trace_mod.SpanKind = _SpanKind
    trace_mod.StatusCode = _StatusCode
    trace_mod.TraceFlags = _TraceFlags
    trace_mod.SpanContext = _SpanContext
    trace_mod.NonRecordingSpan = _NonRecordingSpan
    trace_mod.get_tracer = _get_tracer
    trace_mod.set_span_in_context = lambda span, ctx: ctx
    trace_mod.use_span = MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=None),
        __exit__=MagicMock(return_value=False),
    ))

    # opentelemetry.context
    ctx_mod = types.ModuleType("opentelemetry.context")
    ctx_mod.get_current = lambda: {}

    # opentelemetry.propagate
    prop_mod = types.ModuleType("opentelemetry.propagate")
    prop_mod.inject = MagicMock()
    prop_mod.extract = MagicMock(return_value={})

    otel.trace = trace_mod
    otel.context = ctx_mod
    otel.propagate = prop_mod

    return otel, trace_mod, ctx_mod, _fake_tracer


# ---------------------------------------------------------------------------
# Fixture: patch sys.modules with the fake OTel SDK
# ---------------------------------------------------------------------------


@pytest.fixture()
def otel_mocks():
    """Inject a fake opentelemetry namespace into sys.modules."""
    otel, trace_mod, ctx_mod, tracer = _build_otel_mock()
    modules_to_patch = {
        "opentelemetry": otel,
        "opentelemetry.trace": trace_mod,
        "opentelemetry.context": ctx_mod,
    }
    with patch.dict(sys.modules, modules_to_patch):
        yield otel, trace_mod, ctx_mod, tracer


# ---------------------------------------------------------------------------
# Import guard — no SDK
# ---------------------------------------------------------------------------


class TestImportGuard:
    def test_constructor_fails_without_opentelemetry(self) -> None:
        """OTelBridgeExporter raises ImportError when opentelemetry is absent."""
        # Force opentelemetry to appear uninstalled.
        with patch.dict(sys.modules, {"opentelemetry": None}):
            from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

            with pytest.raises(ImportError, match="opentelemetry-sdk"):
                OTelBridgeExporter()


# ---------------------------------------------------------------------------
# OTelBridgeExporter — constructor & repr
# ---------------------------------------------------------------------------


class TestOTelBridgeExporterInit:
    def test_constructor_succeeds_with_mocked_sdk(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        assert bridge._tracer_name == "agentobs"
        assert bridge._tracer_version == "1.0"

    def test_custom_tracer_name_and_version(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter(tracer_name="my-app", tracer_version="2.0")
        assert bridge._tracer_name == "my-app"
        assert bridge._tracer_version == "2.0"

    def test_repr_contains_tracer_name(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter(tracer_name="my-app")
        assert "my-app" in repr(bridge)


# ---------------------------------------------------------------------------
# _build_otel_attributes
# ---------------------------------------------------------------------------


class TestBuildOtelAttributes:
    def test_llm_namespace_attributes_present(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(
            trace_id="a" * 32,
            payload={"status": "ok", "span_name": "run"},
        )
        attrs = bridge._build_otel_attributes(event)
        assert "llm.event_type" in attrs
        assert "llm.source" in attrs
        assert "llm.event_id" in attrs

    def test_tags_are_included(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(
            tags=Tags(env="prod", region="us-east-1"),
            payload={"status": "ok"},
        )
        attrs = bridge._build_otel_attributes(event)
        assert attrs.get("llm.tag.env") == "prod"
        assert attrs.get("llm.tag.region") == "us-east-1"

    def test_org_id_included_when_set(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = Event(
            event_type="llm.trace.span.completed",
            source="test@1.0.0",
            payload={"status": "ok"},
            org_id="acme",
            team_id="platform",
            actor_id="user-42",
            session_id="sess-1",
        )
        attrs = bridge._build_otel_attributes(event)
        assert attrs["llm.org_id"] == "acme"
        assert attrs["llm.team_id"] == "platform"
        assert attrs["llm.actor_id"] == "user-42"
        assert attrs["llm.session_id"] == "sess-1"

    def test_gen_ai_system_from_model_provider(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(
            payload={
                "status": "ok",
                "model": {"name": "gpt-4o", "provider": "openai"},
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
        attrs = bridge._build_otel_attributes(event)
        assert attrs.get("gen_ai.system") == "openai"
        assert attrs.get("gen_ai.request.model") == "gpt-4o"
        assert attrs.get("gen_ai.usage.input_tokens") == 10
        assert attrs.get("gen_ai.usage.output_tokens") == 5

    def test_checksum_included_when_set(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = Event(
            event_type="llm.trace.span.completed",
            source="test@1.0.0",
            payload={"status": "ok"},
            checksum="sha256:" + "a" * 64,
        )
        attrs = bridge._build_otel_attributes(event)
        assert "llm.checksum" in attrs

    def test_scalar_payload_values_included(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(payload={"status": "ok", "cost": 0.01, "flag": True, "count": 3})
        attrs = bridge._build_otel_attributes(event)
        assert attrs.get("llm.payload.cost") == pytest.approx(0.01)
        assert attrs.get("llm.payload.flag") is True
        assert attrs.get("llm.payload.count") == 3


# ---------------------------------------------------------------------------
# _resolve_span_context
# ---------------------------------------------------------------------------


class TestResolveSpanContext:
    def test_no_trace_id_returns_none(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(payload={"status": "ok"})
        assert bridge._resolve_span_context(event) is None

    def test_trace_id_with_span_id_returns_span_context(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(
            trace_id="a" * 32,
            span_id="b" * 16,
            payload={"status": "ok"},
        )
        result = bridge._resolve_span_context(event)
        assert result is not None

    def test_trace_id_with_parent_span_id_uses_parent(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(
            trace_id="a" * 32,
            parent_span_id="c" * 16,
            span_id="b" * 16,
            payload={"status": "ok"},
        )
        result = bridge._resolve_span_context(event)
        assert result is not None

    def test_trace_id_without_span_id_returns_none(self, otel_mocks: Any) -> None:
        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(trace_id="a" * 32, payload={"status": "ok"})
        # No span_id, no parent_span_id → resolve returns None
        result = bridge._resolve_span_context(event)
        assert result is None


# ---------------------------------------------------------------------------
# export / export_batch
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_ok_event_ends_span(self, otel_mocks: Any) -> None:
        import asyncio  # noqa: PLC0415

        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(
            trace_id="a" * 32,
            span_id="b" * 16,
            payload={"status": "ok", "span_name": "run"},
        )
        asyncio.run(bridge.export(event))

    def test_export_error_event_sets_error_status(self, otel_mocks: Any) -> None:
        import asyncio  # noqa: PLC0415

        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(
            trace_id="a" * 32,
            span_id="b" * 16,
            payload={"status": "error", "error": "model timeout"},
        )
        asyncio.run(bridge.export(event))

    def test_export_batch_processes_all_events(self, otel_mocks: Any) -> None:
        import asyncio  # noqa: PLC0415

        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        events = [
            _make_event(trace_id="a" * 32, span_id="b" * 16, payload={"status": "ok"})
            for _ in range(5)
        ]
        asyncio.run(bridge.export_batch(events))

    def test_export_event_without_trace_id(self, otel_mocks: Any) -> None:
        import asyncio  # noqa: PLC0415

        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(payload={"status": "ok"})
        asyncio.run(bridge.export(event))

    def test_export_timeout_event(self, otel_mocks: Any) -> None:
        import asyncio  # noqa: PLC0415

        from agentobs.export.otel_bridge import OTelBridgeExporter  # noqa: PLC0415

        bridge = OTelBridgeExporter()
        event = _make_event(
            trace_id="a" * 32,
            span_id="b" * 16,
            payload={"status": "timeout"},
        )
        asyncio.run(bridge.export(event))
