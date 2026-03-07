"""agentobs._stream — Internal synchronous event emitter.

This module is the bridge between the tracer's context managers and the
configured export backend.  It is intentionally private — user code should
interact with the tracer, not this module directly.

Flow
----
::

    Span.__exit__
      → _stream.emit_span(span)
        → build SpanPayload
        → build Event(event_type=TRACE_SPAN_COMPLETED, payload=span_payload.to_dict())
        → _active_exporter().export(event)   ← sync

The active exporter is resolved lazily on first use and cached until the
config changes (call :func:`_reset_exporter` after ``configure()``).
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import warnings

from agentobs.config import AgentOBSConfig, get_config
from agentobs.event import Event, Tags
from agentobs.exceptions import ExportError
from agentobs.types import EventType

__all__: list[str] = []  # internal — not re-exported from agentobs root

_export_logger = logging.getLogger("agentobs.export")

# Thread-safe export error counter (useful for metrics / health checks).
_export_error_count: int = 0
_export_error_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Source field sanitisation
# ---------------------------------------------------------------------------

_SOURCE_START_RE = re.compile(r"^[a-zA-Z]")
_SOURCE_BODY_RE = re.compile(r"[^a-zA-Z0-9._\-]")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


def _build_source(service_name: str, service_version: str) -> str:
    """Return a valid ``name@version`` source string.

    Sanitise ``service_name`` so it always starts with a letter and contains
    only ``[a-zA-Z0-9._-]``.  Ensures ``service_version`` looks like a semver.
    """
    name = _SOURCE_BODY_RE.sub("-", service_name)
    if not _SOURCE_START_RE.match(name):
        name = "s" + name  # prepend 's' if name starts with a digit/special char
    if not _VERSION_RE.match(service_version):
        service_version = "0.0.0"
    return f"{name}@{service_version}"


# ---------------------------------------------------------------------------
# Exporter resolution
# ---------------------------------------------------------------------------

_exporter_lock = threading.Lock()
_cached_exporter: object | None = None  # SyncExporter protocol instance

# ---------------------------------------------------------------------------
# Signing chain state
# ---------------------------------------------------------------------------

_sign_lock = threading.Lock()
_prev_signed_event: Event | None = None  # last event in the HMAC chain


def _handle_export_error(exc: Exception) -> None:
    """Apply the configured ``on_export_error`` policy for *exc*.

    Policies:

    - ``"drop"``  — silently discard the error (opt-in to original behaviour).
    - ``"warn"``  — emit a :mod:`warnings` ``UserWarning`` (default).
    - ``"raise"`` — re-raise the exception into caller code.
    """
    global _export_error_count  # noqa: PLW0603
    with _export_error_lock:
        _export_error_count += 1

    _export_logger.warning(
        "agentobs export error (%s): %s",
        type(exc).__name__,
        exc,
    )

    try:
        policy = get_config().on_export_error
    except Exception:
        policy = "warn"  # safe fallback if config itself is broken

    if policy == "raise":
        raise exc
    if policy == "warn":
        warnings.warn(
            f"agentobs export error ({type(exc).__name__}): {exc}",
            stacklevel=3,
        )
    # "drop": discard silently


def _reset_exporter() -> None:
    """Invalidate the cached exporter and reset the HMAC signing chain."""
    global _cached_exporter, _prev_signed_event  # noqa: PLW0603
    with _exporter_lock:
        if _cached_exporter is not None:
            # Flush + close any open file handles before discarding the exporter.
            try:
                if hasattr(_cached_exporter, "close"):
                    _cached_exporter.close()  # type: ignore[union-attr]
            except Exception as exc:
                _handle_export_error(exc)
        _cached_exporter = None
    with _sign_lock:
        _prev_signed_event = None
    # Recreate the trace store with the (possibly updated) size from config.
    try:
        from agentobs._store import _reset_store  # noqa: PLC0415
        from agentobs.config import get_config as _gc  # noqa: PLC0415
        _reset_store(_gc().trace_store_size)
    except Exception:
        pass  # never let store reset failures affect the exporter reset


def _active_exporter() -> object:
    """Return the cached exporter, instantiating it from config if necessary."""
    global _cached_exporter  # noqa: PLW0603
    if _cached_exporter is not None:
        return _cached_exporter
    with _exporter_lock:
        if _cached_exporter is not None:
            return _cached_exporter
        _cached_exporter = _build_exporter()
    return _cached_exporter


def _build_exporter() -> object:
    """Instantiate the correct exporter based on the current config."""
    cfg = get_config()
    name = (cfg.exporter or "console").lower()

    if name == "jsonl":
        from agentobs.exporters.jsonl import SyncJSONLExporter  # noqa: PLC0415
        path = cfg.endpoint or "agentobs_events.jsonl"
        return SyncJSONLExporter(path)

    if name == "console":
        from agentobs.exporters.console import SyncConsoleExporter  # noqa: PLC0415
        return SyncConsoleExporter()

    # Named exporters that are only supported via EventStream (async path).
    # Warn the user so they know to switch to EventStream instead of silently
    # receiving console output.
    _supported_via_eventstream = frozenset({"otlp", "webhook", "datadog", "grafana_loki"})
    if name in _supported_via_eventstream:
        warnings.warn(
            f"agentobs: exporter={name!r} is not supported by the synchronous tracer "
            f"(configure / start_trace).  Use agentobs.stream.EventStream with the "
            f"agentobs.export.{name} module instead.  Falling back to console output.",
            UserWarning,
            stacklevel=4,
        )

    # Default fallback: use the console exporter.
    from agentobs.exporters.console import SyncConsoleExporter  # noqa: PLC0415
    return SyncConsoleExporter()


# ---------------------------------------------------------------------------
# Event construction helpers
# ---------------------------------------------------------------------------


def _build_event(
    event_type: EventType,
    payload_dict: dict,
    span_id: str | None = None,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
) -> Event:
    """Construct a fully-populated :class:`~agentobs.event.Event` envelope."""
    cfg = get_config()
    source = _build_source(cfg.service_name, cfg.service_version)

    kwargs: dict = {
        "event_type": event_type,
        "source": source,
        "payload": payload_dict,
    }
    if cfg.org_id:
        kwargs["org_id"] = cfg.org_id
    if span_id:
        kwargs["span_id"] = span_id
    if trace_id:
        kwargs["trace_id"] = trace_id
    if parent_span_id:
        kwargs["parent_span_id"] = parent_span_id

    tags_kwargs: dict = {"env": cfg.env}
    kwargs["tags"] = Tags(**tags_kwargs)

    return Event(**kwargs)


# ---------------------------------------------------------------------------
# Public emit functions (called by _span.py context managers)
# ---------------------------------------------------------------------------


def emit_span(span: object) -> None:
    """Build a ``SpanPayload`` event from *span* and export it.

    Also notifies the active :class:`~agentobs._trace.Trace` collector (if any)
    so it can accumulate spans for :meth:`~agentobs._trace.Trace.to_json`.

    Args:
        span: A :class:`~agentobs._span.Span` instance.
    """
    # Import here to avoid circular import at module load time.
    from agentobs._span import Span, _run_stack_var  # noqa: PLC0415

    assert isinstance(span, Span)
    payload = span.to_span_payload()
    event_type = (
        EventType.TRACE_SPAN_FAILED if span.status == "error"
        else EventType.TRACE_SPAN_COMPLETED
    )
    event = _build_event(
        event_type=event_type,
        payload_dict=payload.to_dict(),
        span_id=span.span_id,
        trace_id=span.trace_id,
        parent_span_id=span.parent_span_id,
    )
    _dispatch(event)

    # Notify the Trace collector (set by start_trace()) so it can accumulate spans.
    run_tuple = _run_stack_var.get()
    if run_tuple:
        collector = getattr(run_tuple[-1], "_trace_collector", None)
        if collector is not None:
            try:
                collector._record_span(span)
            except Exception:
                pass  # never let collection errors affect the main emit path


def emit_agent_step(step: object) -> None:
    """Build an ``AgentStepPayload`` event from *step* and export it."""
    from agentobs._span import AgentStepContext  # noqa: PLC0415

    assert isinstance(step, AgentStepContext)
    payload = step.to_agent_step_payload()
    event = _build_event(
        event_type=EventType.TRACE_AGENT_STEP,
        payload_dict=payload.to_dict(),
        span_id=step.span_id,
        trace_id=step.trace_id,
        parent_span_id=step.parent_span_id,
    )
    _dispatch(event)


def emit_agent_run(run: object) -> None:
    """Build an ``AgentRunPayload`` event from *run* and export it."""
    from agentobs._span import AgentRunContext  # noqa: PLC0415

    assert isinstance(run, AgentRunContext)
    payload = run.to_agent_run_payload()
    event = _build_event(
        event_type=EventType.TRACE_AGENT_COMPLETED,
        payload_dict=payload.to_dict(),
        trace_id=run.trace_id,
        span_id=run.root_span_id,
    )
    _dispatch(event)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _should_emit(event: "Event", cfg: "AgentOBSConfig") -> bool:
    """Return ``True`` if *event* should be exported under the current config.

    The sampling decision is made in this order:

    1. **Error pass-through** — when ``always_sample_errors=True`` (the
       default), spans with ``status="error"`` or ``status="timeout"`` are
       always emitted regardless of *sample_rate*.
    2. **Probabilistic sampling** — the decision is deterministic per
       ``trace_id``: all spans of a given trace are sampled or dropped
       together.  Uses the first 8 hex digits of the trace_id as a
       32-bit hash so the decision is reproducible.
    3. **Custom filters** — all ``trace_filters`` callables must return
       ``True`` for the event to be emitted.

    Args:
        event: The candidate event.
        cfg:   Live :class:`~agentobs.config.AgentOBSConfig` snapshot.

    Returns:
        ``True`` to emit, ``False`` to drop.
    """
    # Fast path: no sampling configured, no filters — always emit.
    if cfg.sample_rate >= 1.0 and not cfg.trace_filters:
        return True

    # Step 1: always emit errors when configured.
    if cfg.always_sample_errors:
        status = event.payload.get("status", "")
        if status in ("error", "timeout"):
            return True

    # Step 2: probabilistic sampling keyed on trace_id.
    if cfg.sample_rate < 1.0:
        trace_id: str = event.payload.get("trace_id", "")
        if trace_id:
            # Use the first 8 hex chars (32 bits) as a cheap deterministic hash.
            token = trace_id[:8]
            try:
                bucket = int(token, 16)
            except ValueError:
                bucket = 0
            # Normalise to [0, 1) and compare against sample_rate.
            if bucket / 0xFFFF_FFFF > cfg.sample_rate:
                return False
        else:
            # No trace_id: apply pure random sampling.
            if random.random() > cfg.sample_rate:
                return False

    # Step 3: custom filters (all must pass).
    for f in cfg.trace_filters:
        try:
            if not f(event):
                return False
        except Exception:
            pass  # a failing filter never silently drops the event

    return True


def _dispatch(event: Event) -> None:
    """Export *event* through the active exporter, handling errors per policy.

    Pipeline (in order):
    0. **Sampling** — apply probabilistic sampling and custom filters; drop
       the event immediately if it should not be emitted.
    1. **Redaction** — apply :class:`~agentobs.redact.RedactionPolicy` when
       ``config.redaction_policy`` is set.  PII is masked before anything
       else sees the event.
    2. **Signing** — sign with HMAC-SHA256 and chain to the previous event
       when ``config.signing_key`` is set.
    3. **Export** — hand the event to the active exporter.

    On failure the error is routed through :func:`_handle_export_error` which
    applies the ``on_export_error`` policy (``"warn"`` | ``"raise"`` | ``"drop"``).
    """
    global _prev_signed_event  # noqa: PLW0603
    try:
        cfg = get_config()

        # 0. Sampling — drop early to avoid unnecessary work.
        if not _should_emit(event, cfg):
            return

        # 1. Redaction (must occur before signing so signatures cover
        #    the already-redacted payload).
        if cfg.redaction_policy is not None:
            event = cfg.redaction_policy.apply(event).event

        # 2. Signing — maintain the audit chain.
        if cfg.signing_key:
            from agentobs.signing import sign  # noqa: PLC0415
            with _sign_lock:
                event = sign(
                    event,
                    org_secret=cfg.signing_key,
                    prev_event=_prev_signed_event,
                )
                _prev_signed_event = event

        # 3. Export (with retry + exponential backoff on transient ExportError only).
        exporter = _active_exporter()
        max_retries: int = cfg.export_max_retries
        for attempt in range(max_retries + 1):
            try:
                exporter.export(event)  # type: ignore[attr-defined]
                break
            except ExportError as exc:
                if attempt < max_retries:
                    _export_logger.debug(
                        "agentobs export attempt %d/%d failed (%s): %s — retrying",
                        attempt + 1,
                        max_retries + 1,
                        type(exc).__name__,
                        exc,
                    )
                    time.sleep(0.5 * (2 ** attempt))  # 0.5 s, 1 s, 2 s …
                else:
                    raise  # exhausted — let outer except call _handle_export_error once

        # 4. Trace store (opt-in ring buffer for programmatic querying).
        if cfg.enable_trace_store:
            try:
                from agentobs._store import get_store  # noqa: PLC0415
                get_store().record(event)
            except Exception as exc:
                _handle_export_error(exc)
    except Exception as exc:
        _handle_export_error(exc)


def get_export_error_count() -> int:
    """Return the total number of export errors recorded since process start.

    Useful for health checks and instrumentation::

        from agentobs._stream import get_export_error_count
        assert get_export_error_count() == 0, "export errors detected"
    """
    with _export_error_lock:
        return _export_error_count
