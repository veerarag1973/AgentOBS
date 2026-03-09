"""agentobs.config — Global configuration singleton and ``configure()`` entry point.

The configuration layer is intentionally simple: a single mutable dataclass
backed by a module-level ``threading.Lock`` for safe concurrent mutation.
Environment variables are read once at import time; subsequent calls to
:func:`configure` override individual fields.

Environment variable mapping
-----------------------------
+-----------------------------+-----------------------+
| Env var                     | Config field          |
+=============================+=======================+
| ``AGENTOBS_EXPORTER``       | ``exporter``          |
| ``AGENTOBS_ENDPOINT``       | ``endpoint``          |
| ``AGENTOBS_ORG_ID``         | ``org_id``            |
| ``AGENTOBS_SERVICE_NAME``   | ``service_name``      |
| ``AGENTOBS_ENV``            | ``env``               |
| ``AGENTOBS_SERVICE_VERSION``| ``service_version``   |
| ``AGENTOBS_SIGNING_KEY``    | ``signing_key``       |
| ``AGENTOBS_SAMPLE_RATE``    | ``sample_rate``       |
+-----------------------------+-----------------------+

Usage::

    from agentobs import configure
    configure(exporter="jsonl", service_name="my-agent", endpoint="./events.jsonl")

    from agentobs.config import get_config
    cfg = get_config()
    print(cfg.service_name)   # "my-agent"
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agentobs.event import Event

__all__ = ["AgentOBSConfig", "configure", "get_config"]

# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

_VALID_EXPORTERS = frozenset({"console", "jsonl", "otlp", "webhook", "datadog", "grafana_loki"})


@dataclass
class AgentOBSConfig:
    """Mutable global configuration for the AgentOBS SDK.

    All fields have safe defaults so zero-configuration usage works
    out-of-the-box (``exporter="console"`` prints to stdout).

    Attributes:
        exporter:        Backend to use:  ``"console"`` | ``"jsonl"`` | ``"otlp"``
                         | ``"webhook"`` | ``"datadog"`` | ``"grafana_loki"``.
        endpoint:        Exporter-specific destination
                         (file path for JSONL, URL for OTLP/webhook/Datadog/Loki).
        org_id:          Organisation identifier; included on all emitted events.
        service_name:    Human-readable service name (used in ``source`` field).
                         Must start with a letter and contain only
                         ``[a-zA-Z0-9._-]``.  Defaults to ``"unknown-service"``.
        env:             Deployment environment tag (e.g. ``"production"``).
        service_version: SemVer string for the emitting service.
                         Defaults to ``"0.0.0"``.
        signing_key:     Base64-encoded HMAC-SHA256 key for audit-chain signing.
                         ``None`` disables signing.
        redaction_policy: :class:`~agentobs.redact.RedactionPolicy` instance or
                          ``None`` to disable PII redaction.
        on_export_error: Policy when an exporter or emission error occurs.
                         One of ``"warn"`` (emit to ``stderr``, default),
                         ``"raise"`` (re-raise the exception into caller code),
                         or ``"drop"`` (silently discard).
        include_raw_tool_io: Opt-in flag to include raw tool arguments
                             (``arguments_raw``) and results (``result_raw``)
                             in serialised :class:`~agentobs.namespaces.trace.ToolCall`
                             payloads.  Defaults to ``False`` to prevent
                             accidental PII leakage.  Set programmatically;
                             no corresponding environment variable is provided.
        sample_rate:         Fraction of traces to emit (0.0–1.0 inclusive).
                             Sampling is deterministic per ``trace_id`` so
                             all spans of a trace are sampled together.
                             Defaults to ``1.0`` (emit everything).  Set via
                             ``AGENTOBS_SAMPLE_RATE`` env var.
        always_sample_errors: When ``True`` (the default), spans/traces with
                             ``status="error"`` or ``status="timeout"`` are
                             always emitted regardless of *sample_rate*.
        trace_filters:       List of callables ``(Event) -> bool``.  An event
                             is emitted only when **all** filters return
                             ``True``.  Applied after probabilistic sampling.
                             Not configurable via environment variable.
        enable_trace_store:  When ``True``, every dispatched event is also
                             written to the in-process
                             :class:`~agentobs._store.TraceStore` ring buffer so
                             it can be queried via :func:`~agentobs.get_trace`
                             etc.  Defaults to ``False``.  Set via
                             ``AGENTOBS_ENABLE_TRACE_STORE=1``.
        trace_store_size:    Maximum number of distinct traces the ring buffer
                             retains.  Oldest trace is evicted when full.
                             Default: 100.
        export_max_retries:  Number of retry attempts on transient export failures
                             before the ``on_export_error`` policy is applied.
                             Retries use exponential back-off (0.5 s, 1 s, 2 s …).
                             Default: 3.
        auto_emit_cost:      When ``True``, automatically emit a
                             ``llm.cost.token.recorded`` event whenever a span
                             closes with a non-``None`` ``cost`` attribute.
                             Defaults to ``False``.
        budget_usd_per_run:  When set, a budget alert is fired on the global
                             :class:`~agentobs.cost.CostTracker` when any single
                             agent run accumulates costs exceeding this value.
                             ``None`` disables per-run budget checks.
        budget_usd_per_day:  Rolling 24-hour USD budget cap on the global tracker.
                             ``None`` disables the daily budget check.
    """

    exporter: str = "console"
    endpoint: str | None = None
    org_id: str | None = None
    service_name: str = "unknown-service"
    env: str = "production"
    service_version: str = "0.0.0"
    signing_key: str | None = field(default=None, repr=False)
    redaction_policy: Any = None  # RedactionPolicy | None — avoids circular import
    on_export_error: str = "warn"  # "warn" | "raise" | "drop"
    include_raw_tool_io: bool = False  # opt-in to store raw tool I/O (ToolCall.arguments_raw / result_raw)
    sample_rate: float = 1.0          # 0.0–1.0; fraction of traces to emit
    always_sample_errors: bool = True  # emit error/timeout spans regardless of sample_rate
    trace_filters: list[Callable[["Event"], bool]] = field(default_factory=list)
    enable_trace_store: bool = False   # opt-in in-process trace store
    trace_store_size: int = 100        # ring buffer capacity (number of traces)
    export_max_retries: int = 3        # retry count for transient export failures
    # Tool 2 — Cost Calculation Engine
    auto_emit_cost: bool = False                # auto-emit llm.cost.token.recorded on span close
    budget_usd_per_run: float | None = None     # per-run budget cap (USD)
    budget_usd_per_day: float | None = None     # rolling 24-hour budget cap (USD)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_config: AgentOBSConfig = AgentOBSConfig()
_config_lock: threading.Lock = threading.Lock()


def _load_from_env() -> None:
    """Read environment variables and overlay them onto *_config*."""
    env_map = {
        "AGENTOBS_EXPORTER": "exporter",
        "AGENTOBS_ENDPOINT": "endpoint",
        "AGENTOBS_ORG_ID": "org_id",
        "AGENTOBS_SERVICE_NAME": "service_name",
        "AGENTOBS_ENV": "env",
        "AGENTOBS_SERVICE_VERSION": "service_version",
        "AGENTOBS_SIGNING_KEY": "signing_key",
        "AGENTOBS_ON_EXPORT_ERROR": "on_export_error",
    }
    for env_var, field_name in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            setattr(_config, field_name, value)
    # Numeric env vars need explicit conversion.
    raw_rate = os.environ.get("AGENTOBS_SAMPLE_RATE")
    if raw_rate is not None:
        try:
            rate = float(raw_rate)
        except ValueError:
            rate = 1.0
        _config.sample_rate = max(0.0, min(1.0, rate))
    # Boolean env var: AGENTOBS_ENABLE_TRACE_STORE=1 / true / yes enables the store.
    raw_store = os.environ.get("AGENTOBS_ENABLE_TRACE_STORE")
    if raw_store is not None:
        _config.enable_trace_store = raw_store.strip().lower() in ("1", "true", "yes")


# Apply env vars immediately at import time.
_load_from_env()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_config() -> AgentOBSConfig:
    """Return the active :class:`AgentOBSConfig` singleton.

    The returned object is the *live* singleton — modifications to it will
    affect all subsequent tracer operations.  Prefer :func:`configure` for
    intentional mutations.
    """
    return _config


def configure(**kwargs: Any) -> None:  # noqa: ANN401
    """Mutate the global :class:`AgentOBSConfig` singleton.

    Accepts the same keyword arguments as :class:`AgentOBSConfig` field names.
    Unknown keys raise :exc:`ValueError` immediately.  Calling ``configure()``
    with no arguments is a no-op (safe for idempotent setup scripts).

    Args:
        **kwargs: One or more :class:`AgentOBSConfig` field names and their new values.

    Raises:
        ValueError: If an unknown configuration key is passed.

    Examples::

        configure(exporter="jsonl", endpoint="./events.jsonl")
        configure(service_name="my-agent", env="staging")
        configure(signing_key="base64key==")
    """
    if not kwargs:
        return
    with _config_lock:
        for key, value in kwargs.items():
            if not hasattr(_config, key):
                valid = sorted(vars(_config).keys())
                raise ValueError(
                    f"Unknown agentobs configuration key {key!r}. "
                    f"Valid keys: {valid}"
                )
            setattr(_config, key, value)
        # Invalidate the cached exporter in the stream so the next emit
        # picks up the new configuration.  Import here to avoid circular
        # import at module load time.
        try:
            from agentobs import _stream  # noqa: PLC0415
            _stream._reset_exporter()
        except (ImportError, AttributeError):
            # _stream not yet loaded (e.g. during package init) — safe to skip.
            pass
