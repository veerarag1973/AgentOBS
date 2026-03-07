"""agentobs._hooks — Global span lifecycle hook registry.

Provides a :class:`HookRegistry` for registering callbacks that fire when
spans of specific operation types start or end.  A module-level singleton
``hooks`` is exported from ``agentobs.__init__`` for convenience.

Usage::

    import agentobs

    @agentobs.hooks.on_llm_call
    def log_llm(span) -> None:
        print(f"LLM call started: {span.name!r} model={span.model!r}")

    @agentobs.hooks.on_agent_end
    def audit_agent(span) -> None:
        if span.status == "error":
            alert(f"Agent error: {span.error}")

Hook callbacks receive the :class:`~agentobs._span.Span` object.  Start
hooks fire in ``SpanContextManager.__enter__`` (before the body executes);
end hooks fire in ``SpanContextManager.__exit__`` (after the body, before
export).

**Thread safety**: ``HookRegistry`` uses a ``threading.RLock`` so hooks can
be registered from any thread.  Hook *callbacks themselves* are called on
whatever thread the span context manager runs on — no locking is applied
inside the callback invocations.

**Error isolation**: if a hook raises an exception the error is suppressed
(emitted via ``warnings.warn``) so that hook failures never abort user code.
"""

from __future__ import annotations

import threading
import warnings
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agentobs._span import Span

__all__ = ["HookRegistry", "hooks"]

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

HookFn = Callable[["Span"], None]

# Hook kind constants — match the operation strings used in SpanPayload.
_HOOK_AGENT_START = "agent_start"
_HOOK_AGENT_END = "agent_end"
_HOOK_LLM_CALL = "llm_call"
_HOOK_TOOL_CALL = "tool_call"

# Map span operation values → hook kind (for "start" hooks the same mapping is
# used; the distinction between start and end is made by the context manager).
_LLM_OPERATIONS = frozenset({"chat", "completion", "embedding", "chat_completion", "generate"})
_TOOL_OPERATIONS = frozenset({"tool_call"})
_AGENT_OPERATIONS = frozenset({"invoke_agent", "agent"})


def _classify_span(span: "Span") -> str | None:
    """Return the hook kind for *span*, or ``None`` if no hook applies."""
    op = str(getattr(span, "operation", "") or "")
    if op in _LLM_OPERATIONS or op == "chat":
        return _HOOK_LLM_CALL
    if op in _TOOL_OPERATIONS:
        return _HOOK_TOOL_CALL
    if op in _AGENT_OPERATIONS:
        return _HOOK_AGENT_START  # caller differentiates start/end
    # Fallback: if the span name contains a hint use that.
    name = str(getattr(span, "name", "") or "")
    if "llm" in name.lower() or "model" in name.lower():
        return _HOOK_LLM_CALL
    if "tool" in name.lower():
        return _HOOK_TOOL_CALL
    return None


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------


class HookRegistry:
    """Registry of span lifecycle hooks.

    Each ``on_*`` method can be used as a **decorator** or called directly
    to register a callback:

    .. code-block:: python

        @hooks.on_llm_call
        def my_hook(span): ...

        # equivalent:
        hooks.on_llm_call(my_hook)

    Methods:
        on_agent_start: Register a callback fired when any agent-operation span **starts**.
        on_agent_end:   Register a callback fired when any agent-operation span **ends**.
        on_llm_call:    Register a callback fired at both start and end of LLM spans.
        on_tool_call:   Register a callback fired at both start and end of tool spans.
        clear:          Remove all registered hooks.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._hooks: dict[str, list[HookFn]] = {
            _HOOK_AGENT_START: [],
            _HOOK_AGENT_END: [],
            _HOOK_LLM_CALL: [],
            _HOOK_TOOL_CALL: [],
        }

    # ------------------------------------------------------------------
    # Registration decorators / methods
    # ------------------------------------------------------------------

    def on_agent_start(self, fn: HookFn) -> HookFn:
        """Register *fn* to fire when an agent-operation span **starts**.

        Can be used as a decorator::

            @hooks.on_agent_start
            def cb(span): ...
        """
        with self._lock:
            self._hooks[_HOOK_AGENT_START].append(fn)
        return fn

    def on_agent_end(self, fn: HookFn) -> HookFn:
        """Register *fn* to fire when an agent-operation span **ends**."""
        with self._lock:
            self._hooks[_HOOK_AGENT_END].append(fn)
        return fn

    def on_llm_call(self, fn: HookFn) -> HookFn:
        """Register *fn* to fire on LLM-operation spans (start **and** end)."""
        with self._lock:
            self._hooks[_HOOK_LLM_CALL].append(fn)
        return fn

    def on_tool_call(self, fn: HookFn) -> HookFn:
        """Register *fn* to fire on tool-call spans (start **and** end)."""
        with self._lock:
            self._hooks[_HOOK_TOOL_CALL].append(fn)
        return fn

    def clear(self) -> None:
        """Unregister all hooks."""
        with self._lock:
            for key in self._hooks:
                self._hooks[key].clear()

    # ------------------------------------------------------------------
    # Internal fire helpers (called by SpanContextManager)
    # ------------------------------------------------------------------

    def _fire_start(self, span: "Span") -> None:
        """Fire the appropriate start hooks for *span*."""
        kind = _classify_span(span)
        if kind is None:
            return
        if kind in (_HOOK_LLM_CALL, _HOOK_TOOL_CALL):
            self._fire(kind, span)
        elif kind == _HOOK_AGENT_START:
            self._fire(_HOOK_AGENT_START, span)

    def _fire_end(self, span: "Span") -> None:
        """Fire the appropriate end hooks for *span*."""
        kind = _classify_span(span)
        if kind is None:
            return
        if kind in (_HOOK_LLM_CALL, _HOOK_TOOL_CALL):
            self._fire(kind, span)
        elif kind == _HOOK_AGENT_START:
            # Re-use agent_end bucket for end hooks.
            self._fire(_HOOK_AGENT_END, span)

    def _fire(self, kind: str, span: "Span") -> None:
        with self._lock:
            callbacks = list(self._hooks.get(kind, []))
        for cb in callbacks:
            try:
                cb(span)
            except Exception as exc:
                try:
                    warnings.warn(
                        f"agentobs hook error in {cb!r}: {exc}",
                        UserWarning,
                        stacklevel=2,
                    )
                except Exception:
                    pass  # if warn itself raises (e.g. treated as error), ignore

    def __repr__(self) -> str:
        with self._lock:
            counts = {k: len(v) for k, v in self._hooks.items()}
        return f"HookRegistry({counts})"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

hooks: HookRegistry = HookRegistry()
"""Global singleton :class:`HookRegistry` — import and use directly::

    from agentobs import hooks

    @hooks.on_llm_call
    def my_callback(span): ...
"""
