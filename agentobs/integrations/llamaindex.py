"""agentobs.integrations.llamaindex — LlamaIndex event handler.

Provides :class:`LLMSchemaEventHandler`, a LlamaIndex-compatible callback
handler that records LLM, tool-call, and query activity as AgentOBS events.

Usage::

    from agentobs.integrations.llamaindex import LLMSchemaEventHandler

    handler = LLMSchemaEventHandler(source="rag-app", org_id="org-2")

    Settings.callback_manager = CallbackManager([handler])
    # or inject via the query engine constructor

    for event in handler.events:
        print(event.to_json())
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from agentobs.event import Event
from agentobs.ulid import generate as gen_ulid

__all__ = [
    "LLMSchemaEventHandler",
    "is_patched",
    "unpatch",
]

# ---------------------------------------------------------------------------
# Module resolver
# ---------------------------------------------------------------------------


def _require_llamaindex() -> Any:  # noqa: ANN401
    """Return the LlamaIndex callbacks module.

    Tries ``llama_index.core.callbacks`` first (preferred, modern API), then
    falls back to the legacy ``llama_index.callbacks`` module.

    Raises:
        ImportError: If neither ``llama_index.core`` nor ``llama_index`` is installed.
    """
    # Try modern llama_index.core first.
    try:
        import sys  # noqa: PLC0415

        import llama_index.core  # noqa: PLC0415
        import llama_index.core.callbacks  # noqa: PLC0415
        return sys.modules["llama_index.core.callbacks"]
    except ImportError:
        pass
    # Fall back to legacy llama_index package.
    try:
        import sys  # noqa: PLC0415

        import llama_index  # noqa: PLC0415
        import llama_index.callbacks  # noqa: PLC0415, F401
        return sys.modules["llama_index.callbacks"]
    except ImportError:
        pass
    raise ImportError(
        "LlamaIndex package is required for the agentobs LlamaIndex integration.\n"
        "Install it with: pip install 'agentobs[llamaindex]'"
    )


# ---------------------------------------------------------------------------
# Event type mapping
# ---------------------------------------------------------------------------

#: Maps LlamaIndex CBEventType string → (start_event_type, end_event_type)
_CB_TYPE_MAP: dict[str, tuple[str, str]] = {
    "LLM": ("llm.trace.span.started", "llm.trace.span.completed"),
    "FUNCTION_CALL": ("llm.trace.tool_call.started", "llm.trace.tool_call.completed"),
    "QUERY": ("llm.trace.query.started", "llm.trace.query.completed"),
    "RETRIEVE": ("llm.trace.retrieve.started", "llm.trace.retrieve.completed"),
}


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------


class LLMSchemaEventHandler:
    """LlamaIndex callback handler that emits AgentOBS events.

    Compatible with both ``llama_index.core`` and legacy ``llama_index``
    SDK versions.  Events are accumulated in :attr:`events` and optionally
    forwarded to an async exporter.

    Args:
        source:   Value for ``Event.source`` (e.g. ``"rag-app@1.0.0"``).
        org_id:   Optional organisation identifier.
        exporter: Optional async exporter with an ``export(event)``
                  coroutine method.  Export is scheduled via ``create_task``
                  when the event loop is running.

    Attributes:
        events: List of all :class:`~agentobs.event.Event` objects emitted by
                this handler in chronological order.
    """

    def __init__(
        self,
        source: str,
        *,
        org_id: str | None = None,
        exporter: Any | None = None,  # noqa: ANN401
    ) -> None:
        self._source = source
        self._org_id = org_id
        self._exporter = exporter
        self.events: list[Event] = []
        # Tracks start monotonic time by event_id
        self._start_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cb_event_type_str(event_type: Any) -> str:  # noqa: ANN401
        """Convert a LlamaIndex CBEventType (or string) to a plain string.

        If *event_type* has a ``value`` attribute (i.e. it is an ``Enum``),
        the ``str`` representation of that value is returned.  Otherwise the
        object is converted to ``str`` directly.

        Args:
            event_type: A ``CBEventType`` enum member or plain string.

        Returns:
            The string representation of the event type.
        """
        if hasattr(event_type, "value"):
            return str(event_type.value)
        return str(event_type)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_event(self, event_type: str, payload: dict[str, Any]) -> Event:
        """Create a AgentOBS event, append it, and optionally schedule async export.

        Args:
            event_type: Dotted event type string.
            payload:    Event payload dict.

        Returns:
            The newly created :class:`~agentobs.event.Event`.
        """
        event = Event(
            event_type=event_type,
            source=self._source,
            org_id=self._org_id,
            payload=payload,
            event_id=gen_ulid(),
        )
        self.events.append(event)

        if self._exporter is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._exporter.export(event))  # noqa: RUF006
            except RuntimeError:
                pass  # no event loop configured

        return event

    def _duration_ms(self, event_id: str) -> float | None:
        """Return elapsed milliseconds since *event_id* was started, or ``None``.

        Args:
            event_id: The event identifier recorded in :attr:`_start_times`.

        Returns:
            Duration in milliseconds, or ``None`` if *event_id* was not found.
        """
        start = self._start_times.pop(event_id, None)
        if start is None:
            return None
        return (time.monotonic() - start) * 1000.0

    def _start_event_payload(self, et: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Build the payload dict for a start event based on the event type."""
        result: dict[str, Any] = {}
        if et == "LLM":
            model_dict = payload.get("model_dict") or {}
            if isinstance(model_dict, dict):
                result["model"] = model_dict.get("model")
        elif et == "FUNCTION_CALL":
            tool_info = payload.get("tool") or {}
            if isinstance(tool_info, dict):
                result["tool_name"] = tool_info.get("name")
        elif et == "QUERY":
            result["query"] = payload.get("query_str")
        return result

    def _end_event_payload(self, et: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Build the payload dict for an end event based on the event type."""
        result: dict[str, Any] = {}
        if et == "LLM":
            response = payload.get("response")
            raw = getattr(response, "raw", None) if response is not None else None
            if isinstance(raw, dict):
                usage = raw.get("usage") or {}
                if isinstance(usage, dict):
                    result["prompt_tokens"] = usage.get("prompt_tokens")
                    result["completion_tokens"] = usage.get("completion_tokens")
                    result["total_tokens"] = usage.get("total_tokens")
        elif et == "FUNCTION_CALL":
            result["output"] = payload.get("output")
        elif et == "QUERY":
            result["response"] = str(payload.get("response", ""))[:2048]
        return result

    # ------------------------------------------------------------------
    # LlamaIndex callback interface
    # ------------------------------------------------------------------

    def on_event_start(
        self,
        event_type: Any,  # noqa: ANN401
        *,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> str:
        """Called when a LlamaIndex callback event begins.

        Args:
            event_type: A ``CBEventType`` enum member or plain string.
            payload:    Optional dict of event-specific data.
            event_id:   Optional identifier for this event; generated if absent.
            **kwargs:   Additional keyword arguments (ignored).

        Returns:
            The ``event_id`` (passed through or generated).
        """
        if event_id is None:
            event_id = gen_ulid()

        et = self._cb_event_type_str(event_type)
        type_map = _CB_TYPE_MAP.get(et)
        if type_map is None:
            # Unknown event type — record start time but emit nothing.
            self._start_times[event_id] = time.monotonic()
            return event_id

        self._start_times[event_id] = time.monotonic()

        start_et, _ = type_map
        payload = payload or {}
        event_payload: dict[str, Any] = {"event_id": event_id, **self._start_event_payload(et, payload)}

        self._make_event(start_et, event_payload)
        return event_id

    def on_event_end(
        self,
        event_type: Any,  # noqa: ANN401
        *,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Called when a LlamaIndex callback event ends.

        Args:
            event_type: A ``CBEventType`` enum member or plain string.
            payload:    Optional dict of event-specific data (e.g. response).
            event_id:   Identifies which started event this ends.
            **kwargs:   Additional keyword arguments (ignored).
        """
        et = self._cb_event_type_str(event_type)
        type_map = _CB_TYPE_MAP.get(et)
        if type_map is None:
            # Unknown event type — consume any pending start time silently.
            if event_id:
                self._start_times.pop(event_id, None)
            return

        _, end_et = type_map
        duration: float | None = self._duration_ms(event_id) if event_id else None
        payload = payload or {}
        event_payload: dict[str, Any] = {
            "event_id": event_id,
            "duration_ms": duration,
            **self._end_event_payload(et, payload),
        }

        self._make_event(end_et, event_payload)

    def start_trace(self, trace_id: str | None = None, **kwargs: Any) -> None:  # noqa: ANN401
        """No-op — LlamaIndex trace lifecycle hook.

        Args:
            trace_id: Ignored.
            **kwargs: Ignored.
        """
        pass  # intentionally empty

    def end_trace(
        self,
        trace_id: str | None = None,
        trace_map: dict[str, Any] | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """No-op — LlamaIndex trace lifecycle hook.

        Args:
            trace_id:  Ignored.
            trace_map: Ignored.
            **kwargs:  Ignored.
        """
        pass  # intentionally empty

    def clear_events(self) -> None:
        """Remove all accumulated events from :attr:`events`."""
        self.events.clear()

    # ------------------------------------------------------------------
    # dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"LLMSchemaEventHandler("
            f"source={self._source!r}, "
            f"events={len(self.events)})"
        )


# ---------------------------------------------------------------------------
# Module-level patch / unpatch API (for API consistency with other integrations)
# ---------------------------------------------------------------------------

def is_patched() -> bool:
    """Return ``True`` if the LlamaIndex integration module is importable.

    LlamaIndex uses a callback-handler pattern rather than monkey-patching, so
    there is no global state to check.  This function simply verifies that the
    LlamaIndex package is available.

    Returns:
        ``True`` when LlamaIndex is installed and the handler can be created.
    """
    try:
        _require_llamaindex()
        return True
    except ImportError:
        return False


def unpatch() -> None:
    """No-op for the LlamaIndex integration.

    LlamaIndex uses an explicit callback handler (not monkey-patching), so
    there is nothing to undo globally.  Remove the handler from your
    ``CallbackManager`` to stop receiving events::

        Settings.callback_manager = CallbackManager([])  # remove all handlers
    """
