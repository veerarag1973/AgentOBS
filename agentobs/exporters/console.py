"""agentobs.exporters.console — Human-readable development console exporter.

Prints a formatted summary box to ``sys.stdout`` each time a span or agent
event is emitted.  Designed for rapid development feedback — no file is written,
no external dependencies are required.

Example output (with colour support)::

    ╔══ span: chat [gpt-4o] ══════════════════════════════╗
    ║  event_id   : 01JXXXXXXXXXXXXXXXXXXXXXXX
    ║  trace_id   : 01JXXXXXXXXXXXXXXXXXXXXXXX
    ║  duration   : 142.3ms
    ║  tokens     : in=512  out=128  total=640
    ║  cost        : $0.00096
    ║  status     : ok
    ╚═════════════════════════════════════════════════════╝

Colour is enabled automatically when stdout is a TTY.  Set the ``NO_COLOR``
environment variable (any value) to force plain text output per the
`no-color.org <https://no-color.org>`_ convention.

Usage::

    from agentobs import configure
    configure(exporter="console")

Zero external dependencies — stdlib only (``os``, ``sys``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentobs.event import Event

__all__ = ["SyncConsoleExporter"]

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

# ANSI escape codes
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_MAGENTA = "\x1b[35m"
_BLUE = "\x1b[34m"
_WHITE = "\x1b[97m"


def _use_colour() -> bool:
    """Return ``True`` if ANSI colour should be emitted."""
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(text: str, *codes: str) -> str:
    """Wrap *text* with ANSI *codes* if colour is enabled, else return plain."""
    if not _use_colour():
        return text
    return "".join(codes) + text + _RESET


# ---------------------------------------------------------------------------
# Box-drawing characters
# ---------------------------------------------------------------------------

_BOX_WIDTH = 56  # inner width (chars between ╔ and ╗)
_MIN_NAMESPACE_PARTS = 2  # minimum dot-separated parts for namespace extraction

_TL = "╔"  # top-left corner
_TR = "╗"  # top-right corner
_BL = "╚"  # bottom-left corner
_BR = "╝"  # bottom-right corner
_H = "═"   # horizontal
_V = "║"   # vertical
_TJ = "╤"  # top T-junction (unused, reserved)


def _hline(char: str = _H) -> str:
    return char * _BOX_WIDTH


def _top_bar(title: str) -> str:
    """``╔══ <title> ═════╗`` with padding."""
    inner = f"══ {title} "
    pad = _BOX_WIDTH - len(inner)
    pad = max(pad, 2)
    return _c(_TL + inner + _H * pad + _TR, _CYAN, _BOLD)


def _bottom_bar() -> str:
    return _c(_BL + _hline() + _BR, _CYAN, _BOLD)


def _row(label: str, value: str, value_colour: str = "") -> str:
    label_part = _c(f"  {label:<12}", _DIM)
    colon = _c(": ", _DIM)
    val_part = _c(value, value_colour) if value_colour else value
    return _c(_V, _CYAN, _BOLD) + label_part + colon + val_part


# ---------------------------------------------------------------------------
# Payload extractors
# ---------------------------------------------------------------------------


def _get(payload: dict, *keys: str, default: str = "") -> str:
    """Safely retrieve a nested value from *payload* as a string.

    Works with both plain ``dict`` and read-only ``MappingProxyType`` objects
    (the latter is how :class:`~agentobs.event.Event` stores its payload).
    """
    obj: object = payload
    for key in keys:
        if not isinstance(obj, Mapping):
            return default
        obj = obj.get(key)  # type: ignore[union-attr]
    if obj is None:
        return default
    return str(obj)


def _format_tokens(payload: dict) -> str | None:
    tu = payload.get("token_usage")
    if not isinstance(tu, dict):
        return None
    i = tu.get("input_tokens", "?")
    o = tu.get("output_tokens", "?")
    t = tu.get("total_tokens", "?")
    return f"in={i}  out={o}  total={t}"


def _format_cost(payload: dict) -> str | None:
    cost = payload.get("cost")
    if not isinstance(cost, dict):
        return None
    total = cost.get("total_cost_usd")
    if total is None:
        return None
    currency = cost.get("currency", "USD")
    if currency == "USD":
        return f"${total:.5f}"
    return f"{total:.5f} {currency}"


def _format_duration(payload: dict) -> str | None:
    ms = payload.get("duration_ms")
    if ms is None:
        return None
    return f"{float(ms):.1f}ms"


def _status_colour(status: str) -> str:
    if status == "ok":
        return _GREEN
    if status in ("error", "timeout"):
        return _RED
    return _YELLOW


# ---------------------------------------------------------------------------
# Main formatter
# ---------------------------------------------------------------------------


def _format_event(event: Event) -> str:
    """Render *event* as a multi-line console box string."""
    payload = event.payload or {}
    et = event.event_type  # e.g. "llm.trace.span.completed"

    # Determine a compact title from event type + span/agent name.
    et.split(".")[-1] if "." in et else et  # completed / step / etc.
    namespace_part = et.split(".")[2] if et.count(".") >= _MIN_NAMESPACE_PARTS else "trace"
    span_name = (
        payload.get("span_name")
        or payload.get("agent_name")
        or payload.get("step_name")
        or "unknown"
    )
    model_name = _get(payload, "model", "name")
    model_suffix = f" [{model_name}]" if model_name else ""
    title = f"{namespace_part}: {span_name}{model_suffix}"

    lines: list[str] = [_top_bar(title)]

    # Core identifiers.
    lines.append(_row("event_id", event.event_id, _BLUE))
    lines.append(_row("event_type", et))

    trace_id = event.trace_id or _get(payload, "trace_id")
    if trace_id:
        lines.append(_row("trace_id", trace_id, _MAGENTA))
    span_id = event.span_id or _get(payload, "span_id")
    if span_id:
        lines.append(_row("span_id", span_id, _MAGENTA))

    # Duration.
    dur = _format_duration(payload)
    if dur:
        lines.append(_row("duration", dur, _CYAN))

    # Token usage.
    tokens = _format_tokens(payload)
    if tokens:
        lines.append(_row("tokens", tokens, _WHITE))

    # Cost.
    cost_str = _format_cost(payload)
    if cost_str:
        lines.append(_row("cost", cost_str, _YELLOW))

    # Status.
    status = payload.get("status", "ok")
    if isinstance(status, str):
        lines.append(_row("status", status, _status_colour(status)))

    # Error (if any).
    error_msg = payload.get("error")
    if error_msg:
        lines.append(_row("error", str(error_msg), _RED))

    # Agent-specific: step count, total_steps.
    if "total_steps" in payload:
        lines.append(_row("steps", str(payload["total_steps"])))
    if "step_index" in payload:
        lines.append(_row("step_index", str(payload["step_index"])))

    lines.append(_bottom_bar())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Exporter class
# ---------------------------------------------------------------------------


class SyncConsoleExporter:
    """Synchronous exporter that pretty-prints events to ``sys.stdout``.

    No file is written; output goes to ``sys.stdout`` only.  ANSI colour
    codes are emitted when stdout is a TTY and ``NO_COLOR`` is not set.

    This exporter is the default when ``configure(exporter="console")`` is
    used, which is the default if ``AGENTOBS_EXPORTER`` is not set.
    """

    def export(self, event: Event) -> None:
        """Print *event* as a formatted box to ``sys.stdout``.

        Args:
            event: A :class:`~agentobs.event.Event` instance.
        """
        formatted = _format_event(event)
        sys.stdout.write(formatted)
        sys.stdout.flush()

    def flush(self) -> None:
        """Flush stdout."""
        sys.stdout.flush()

    def close(self) -> None:
        """No-op — console exporter has no resources to release."""

    def __repr__(self) -> str:
        return "SyncConsoleExporter()"
