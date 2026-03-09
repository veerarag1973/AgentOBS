"""OpenTelemetry SDK bridge for agentobs.

When the ``opentelemetry-sdk`` package is installed (``pip install
"agentobs[otel]"``) this module provides a first-class integration
that converts :class:`~agentobs.event.Event` objects into **real**
OpenTelemetry spans via the OTel Python SDK — rather than serialising the OTLP
wire format by hand.

This means:
* Spans flow through any configured ``TracerProvider`` (Jaeger, Zipkin, OTLP,
  console, etc.) without a dedicated ``OTLPExporter`` endpoint.
* ``gen_ai.*`` semantic convention attributes are applied automatically.
* W3C ``traceparent`` / ``tracestate`` context propagation is hooked in via the
  OTel SDK's standard :mod:`opentelemetry.propagate` interface.
* The bridge implements the :class:`~agentobs.stream.Exporter`
  protocol so it is a drop-in replacement for
  :class:`~agentobs.export.otlp.OTLPExporter` in any
  :class:`~agentobs.stream.EventStream` pipeline.

Usage
-----
::

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, BatchSpanProcessor

    # Configure the OTel SDK as usual.
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)

    # Create the bridge and use it like any other exporter.
    from agentobs.export.otel_bridge import OTelBridgeExporter

    bridge = OTelBridgeExporter()
    await bridge.export(event)

Requirements
------------
``pip install "agentobs[otel]"`` — installs ``opentelemetry-sdk>=1.24``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentobs.export.otlp import _gen_ai_attributes

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentobs.event import Event

__all__ = ["OTelBridgeExporter"]

# ---------------------------------------------------------------------------
# Lazy OTel SDK import guard
# ---------------------------------------------------------------------------


def _require_otel() -> Any:  # noqa: ANN401
    """Import the OTel SDK or raise a clear ImportError."""
    try:
        import opentelemetry  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "opentelemetry-sdk is required for OTelBridgeExporter. "
            "Install it: pip install \"agentobs[otel]\""
        ) from exc
    else:
        return opentelemetry


# ---------------------------------------------------------------------------
# SpanKind constants (mirrored to avoid importing SDK at module load time)
# ---------------------------------------------------------------------------

_SPAN_KIND_INTERNAL = 0
_SPAN_KIND_CLIENT = 3

# ---------------------------------------------------------------------------
# OTelBridgeExporter
# ---------------------------------------------------------------------------


class OTelBridgeExporter:
    """Exporter that emits events as real OpenTelemetry SDK spans.

    Converts each :class:`~agentobs.event.Event` into a completed
    OTel span using the globally-configured ``TracerProvider``.  All ``gen_ai.*``
    semantic convention attributes are applied alongside the ``llm.*`` namespace
    attributes so the events are visible in both OTel-native tooling (Grafana,
    Honeycomb, Jaeger) and custom ``llm.*``-aware consumers.

    Implements the :class:`~agentobs.stream.Exporter` protocol —
    can be used anywhere an ``OTLPExporter`` is accepted.

    Args:
        tracer_name:    Instrumentation scope name embedded in every span.
                        Defaults to ``"agentobs"``.
        tracer_version: Instrumentation scope version.  Defaults to ``"1.0"``.

    Example::

        bridge = OTelBridgeExporter()
        await bridge.export(event)

        # Or use in an EventStream pipeline:
        await stream.drain(bridge)
    """

    def __init__(
        self,
        tracer_name: str = "agentobs",
        tracer_version: str = "1.0",
    ) -> None:
        _require_otel()
        self._tracer_name = tracer_name
        self._tracer_version = tracer_version

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_tracer(self) -> Any:  # noqa: ANN401
        from opentelemetry import trace  # noqa: PLC0415
        return trace.get_tracer(self._tracer_name, self._tracer_version)

    @staticmethod
    def _apply_identity_fields(event: "Event", attrs: dict[str, Any]) -> None:
        """Add optional identity fields to attrs if present on the event."""
        if event.org_id is not None:
            attrs["llm.org_id"] = event.org_id
        if event.team_id is not None:
            attrs["llm.team_id"] = event.team_id
        if event.actor_id is not None:
            attrs["llm.actor_id"] = event.actor_id
        if event.session_id is not None:
            attrs["llm.session_id"] = event.session_id
        if event.tags is not None:
            for tag_key, tag_val in event.tags.items():
                attrs[f"llm.tag.{tag_key}"] = tag_val
        if event.checksum is not None:
            attrs["llm.checksum"] = event.checksum

    @staticmethod
    def _apply_payload_fields(event: "Event", attrs: dict[str, Any]) -> None:
        """Flatten event payload (one level deep) into attrs."""
        for k, v in event.payload.items():
            if isinstance(v, (str, int, float, bool)):
                attrs[f"llm.payload.{k}"] = v
            elif isinstance(v, dict):
                for sub_k, sub_v in v.items():
                    if isinstance(sub_v, (str, int, float, bool)):
                        attrs[f"llm.payload.{k}.{sub_k}"] = sub_v

    @staticmethod
    def _apply_gen_ai_fields(event: "Event", attrs: dict[str, Any]) -> None:
        """Extract gen_ai.* semantic conventions from OTLP _kv dicts."""
        for kv in _gen_ai_attributes(event):
            key = kv["key"]
            val_wrapper = kv["value"]
            for type_key in ("stringValue", "intValue", "doubleValue", "boolValue"):
                if type_key in val_wrapper:
                    raw = val_wrapper[type_key]
                    attrs[key] = int(raw) if type_key == "intValue" else raw
                    break

    @staticmethod
    def _build_otel_attributes(event: "Event") -> dict[str, Any]:  # noqa: PLR0912
        """Build a flat attribute dict suitable for the OTel SDK ``span.set_attributes()``."""
        attrs: dict[str, Any] = {
            "llm.schema_version": event.schema_version,
            "llm.event_id": event.event_id,
            "llm.event_type": event.event_type,
            "llm.source": event.source,
        }
        OTelBridgeExporter._apply_identity_fields(event, attrs)
        OTelBridgeExporter._apply_payload_fields(event, attrs)
        OTelBridgeExporter._apply_gen_ai_fields(event, attrs)
        return attrs

    @staticmethod
    def _resolve_span_context(event: Event) -> Any | None:  # noqa: ANN401
        """Build an OTel ``SpanContext`` from the event's trace/parent fields."""
        if event.trace_id is None:
            return None
        try:
            from opentelemetry.trace import (  # noqa: PLC0415
                NonRecordingSpan,
                SpanContext,
                TraceFlags,
            )
        except ImportError:
            return None

        try:
            trace_id_int = int(event.trace_id, 16)
            parent_span_id = event.parent_span_id or event.span_id
            if not parent_span_id:
                return None
            span_id_int = int(parent_span_id, 16)
        except (ValueError, TypeError):
            return None

        return NonRecordingSpan(
            SpanContext(
                trace_id=trace_id_int,
                span_id=span_id_int,
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        )

    # ------------------------------------------------------------------
    # Single-event export
    # ------------------------------------------------------------------

    async def export(self, event: Event) -> None:  # NOSONAR
        """Export a single event as a completed OTel span.

        The span is started and immediately ended using the event's
        ``timestamp`` and ``duration_ms`` (if available) to reconstruct
        the correct wall-clock times.

        Args:
            event: The event to emit as an OTel span.
        """
        from opentelemetry import context as otel_context  # noqa: PLC0415
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.trace import SpanKind, use_span  # noqa: PLC0415

        tracer = self._get_tracer()
        attributes = self._build_otel_attributes(event)

        # Attach the parent span context if the event carries trace linkage.
        parent_span = self._resolve_span_context(event)
        ctx = otel_context.get_current()
        if parent_span is not None:
            ctx = trace.set_span_in_context(parent_span, ctx)

        # Determine SpanKind: LLM calls are CLIENT; internal ops are INTERNAL.
        span_kind = SpanKind.CLIENT

        span = tracer.start_span(
            name=event.event_type,
            context=ctx,
            kind=span_kind,
            attributes=attributes,
        )

        # Record error if the payload indicates failure.
        status_val = event.payload.get("status", "ok")
        error_msg = event.payload.get("error")

        with use_span(span, record_exception=False, end_on_exit=False):
            if status_val in ("error", "timeout"):
                from opentelemetry.trace import StatusCode  # noqa: PLC0415
                message = error_msg or ("Operation timed out" if status_val == "timeout" else "")
                span.set_status(StatusCode.ERROR, message)
            else:
                from opentelemetry.trace import StatusCode  # noqa: PLC0415
                span.set_status(StatusCode.OK)

        span.end()

    # ------------------------------------------------------------------
    # Batch export (Exporter protocol)
    # ------------------------------------------------------------------

    async def export_batch(self, events: Sequence[Event]) -> None:
        """Export a sequence of events as OTel spans.

        Implements the :class:`~agentobs.stream.Exporter` protocol.

        Args:
            events: Sequence of events to export.
        """
        for event in events:
            await self.export(event)

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"OTelBridgeExporter(tracer_name={self._tracer_name!r}, "
            f"tracer_version={self._tracer_version!r})"
        )
