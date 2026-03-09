"""Namespaced event type registry for AGENTOBS SDK (RFC-0001 v2.0).

All built-in event types follow the pattern::

    llm.<namespace>.<entity>.<action>

Third-party extension types MUST use a reverse-domain prefix outside the
``llm.*`` tree (e.g. ``com.example.entity.action``) and MUST NOT claim any
reserved namespace listed in :data:`_RESERVED_NAMESPACES`.

Built-in namespaces (RFC-0001 §7.2)
-------------------------------------

====================  ======================================
Namespace             Purpose
====================  ======================================
``llm.trace.*``       Span tracing, agent runs, reasoning
``llm.cost.*``        Token cost recording and attribution
``llm.cache.*``       Semantic cache hit/miss/eviction
``llm.eval.*``        Evaluation scores and regression
``llm.guard.*``       Input/output safety classifiers
``llm.fence.*``       Structured output constraint loops
``llm.prompt.*``      Prompt rendering and version lifecycle
``llm.redact.*``      PII/PHI detection and redaction audit
``llm.diff.*``        Prompt/response delta analysis
``llm.template.*``    Template registry lifecycle
``llm.audit.*``       HMAC key rotation and chain audit
====================  ======================================

Reserved (future) namespaces (RFC-0001 §7.4)
---------------------------------------------
``llm.rag.*``, ``llm.memory.*``, ``llm.planning.*``,
``llm.multimodal.*``, ``llm.finetune.*``

Design
------
:class:`EventType` is a ``str`` subclass so values can be compared with plain
strings, used as dict keys, and serialised without conversion while still
providing autocomplete and type safety.

:func:`is_registered` and :func:`namespace_of` provide runtime introspection.
:func:`validate_custom` validates third-party extension types.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Final, Literal

from agentobs.exceptions import EventTypeError

__all__ = [
    "EVENT_TYPE_PATTERN",
    "EventType",
    "SpanErrorCategory",
    "is_registered",
    "namespace_of",
    "validate_custom",
]

# ---------------------------------------------------------------------------
# Validation patterns (RFC-0001 §7)
# ---------------------------------------------------------------------------
# Built-in:  llm.<namespace>.<entity>.<action> where namespace is one of the
#            RFC-registered namespaces from §7.2.
# Extension: reverse-domain prefix outside llm.*
#            (e.g. com.example.<entity>.<action>). 
EVENT_TYPE_PATTERN: Final[str] = (
    r"^(?:llm\.(?:trace|cost|cache|eval|guard|fence|prompt|redact|diff|template|audit)\.(?:[a-z][a-z0-9_]*|[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)"
    r"|(?!llm\.)[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)$"
)
_EVENT_TYPE_RE: Final[re.Pattern[str]] = re.compile(EVENT_TYPE_PATTERN)

# RFC-0001 §7.2 — reserved namespaces (built-in).
_RESERVED_NAMESPACES: Final[frozenset[str]] = frozenset(
    [
        "llm.audit",
        "llm.cache",
        "llm.cost",
        "llm.diff",
        "llm.eval",
        "llm.fence",
        "llm.guard",
        "llm.prompt",
        "llm.redact",
        "llm.template",
        "llm.trace",
    ]
)

# RFC-0001 §7.4 — reserved for future standardisation.
_FUTURE_NAMESPACES: Final[frozenset[str]] = frozenset(
    [
        "llm.rag",
        "llm.memory",
        "llm.planning",
        "llm.multimodal",
        "llm.finetune",
    ]
)


class EventType(str, Enum):
    """RFC-0001 Appendix B — canonical AGENTOBS event type registry.

    All 36 first-party event types across 11 namespaces.  Values are the
    canonical wire strings used in serialised events.

    Example::

        et = EventType.TRACE_SPAN_COMPLETED
        assert et == "llm.trace.span.completed"
        assert et.namespace == "llm.trace"
    """

    def __new__(cls, value: str, description: str = "") -> EventType:
        """Construct a new enum member with the given value and description."""
        obj = str.__new__(cls, value)
        obj._value_ = value
        return obj

    def __init__(self, value: str, description: str = "") -> None:
        self._description = description

    def __str__(self) -> str:  # type: ignore[override]
        return self.value  # type: ignore[return-value]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return str.__eq__(self, other)
        return NotImplemented

    def __hash__(self) -> int:
        return str.__hash__(self)

    # ------------------------------------------------------------------
    # llm.trace.*  — RFC-0001 §8.1-§8.5
    # ------------------------------------------------------------------
    TRACE_SPAN_STARTED = (
        "llm.trace.span.started",
        "A new LLM call/tool-execution span was opened.",
    )
    TRACE_SPAN_COMPLETED = (
        "llm.trace.span.completed",
        "A span completed successfully.",
    )
    TRACE_SPAN_FAILED = (
        "llm.trace.span.failed",
        "A span terminated with an error or timeout.",
    )
    TRACE_AGENT_STEP = (
        "llm.trace.agent.step",
        "One iteration of a multi-step agent loop (RFC-0001 §8.4).",
    )
    TRACE_AGENT_COMPLETED = (
        "llm.trace.agent.completed",
        "A multi-step agent run resolved (RFC-0001 §8.5).",
    )
    TRACE_REASONING_STEP = (
        "llm.trace.reasoning.step",
        "One chain-of-thought reasoning step (v2.0+, RFC-0001 §8.2).",
    )

    # ------------------------------------------------------------------
    # llm.cost.*  — RFC-0001 §9.3
    # ------------------------------------------------------------------
    COST_TOKEN_RECORDED = (
        "llm.cost.token.recorded",
        "Per-call token cost recorded.",
    )
    COST_SESSION_RECORDED = (
        "llm.cost.session.recorded",
        "Session-level cost rollup recorded.",
    )
    COST_ATTRIBUTED = (
        "llm.cost.attributed",
        "Cost manually attributed to a feature, team, or budget centre.",
    )

    # ------------------------------------------------------------------
    # llm.cache.*  — RFC-0001 §7.2
    # ------------------------------------------------------------------
    CACHE_HIT = (
        "llm.cache.hit",
        "Semantic cache returned a cached result without a new model call.",
    )
    CACHE_MISS = (
        "llm.cache.miss",
        "Semantic cache lookup found no matching entry.",
    )
    CACHE_EVICTED = (
        "llm.cache.evicted",
        "A cache entry was evicted (TTL, LRU, or manual invalidation).",
    )
    CACHE_WRITTEN = (
        "llm.cache.written",
        "A new entry was written to the semantic cache.",
    )

    # ------------------------------------------------------------------
    # llm.eval.*  — RFC-0001 §7.2
    # ------------------------------------------------------------------
    EVAL_SCORE_RECORDED = (
        "llm.eval.score.recorded",
        "A quality score was attached to a span or agent run.",
    )
    EVAL_REGRESSION_DETECTED = (
        "llm.eval.regression.detected",
        "A quality regression relative to baseline was detected.",
    )
    EVAL_SCENARIO_STARTED = (
        "llm.eval.scenario.started",
        "An evaluation scenario run started.",
    )
    EVAL_SCENARIO_COMPLETED = (
        "llm.eval.scenario.completed",
        "An evaluation scenario run completed.",
    )

    # ------------------------------------------------------------------
    # llm.guard.*  — RFC-0001 §7.2
    # ------------------------------------------------------------------
    GUARD_INPUT_BLOCKED = (
        "llm.guard.input.blocked",
        "A model input was blocked by the safety classifier.",
    )
    GUARD_INPUT_PASSED = (
        "llm.guard.input.passed",
        "A model input passed the safety classifier.",
    )
    GUARD_OUTPUT_BLOCKED = (
        "llm.guard.output.blocked",
        "A model output was blocked by the safety classifier.",
    )
    GUARD_OUTPUT_PASSED = (
        "llm.guard.output.passed",
        "A model output passed the safety classifier.",
    )

    # ------------------------------------------------------------------
    # llm.fence.*  — RFC-0001 §7.2
    # ------------------------------------------------------------------
    FENCE_VALIDATED = (
        "llm.fence.validated",
        "Model output passed all structural constraint checks.",
    )
    FENCE_RETRY_TRIGGERED = (
        "llm.fence.retry.triggered",
        "Model output failed schema validation; retry initiated.",
    )
    FENCE_MAX_RETRIES_EXCEEDED = (
        "llm.fence.max_retries.exceeded",
        "All retry attempts exhausted without conforming output.",
    )

    # ------------------------------------------------------------------
    # llm.prompt.*  — RFC-0001 §7.2
    # ------------------------------------------------------------------
    PROMPT_RENDERED = (
        "llm.prompt.rendered",
        "A prompt template was instantiated with variable values.",
    )
    PROMPT_TEMPLATE_LOADED = (
        "llm.prompt.template.loaded",
        "A prompt template was loaded from the registry.",
    )
    PROMPT_VERSION_CHANGED = (
        "llm.prompt.version.changed",
        "The active version of a prompt template was updated.",
    )

    # ------------------------------------------------------------------
    # llm.redact.*  — RFC-0001 §12
    # ------------------------------------------------------------------
    REDACT_PII_DETECTED = (
        "llm.redact.pii.detected",
        "PII categories were found in one or more event fields.",
    )
    REDACT_PHI_DETECTED = (
        "llm.redact.phi.detected",
        "PHI categories (HIPAA-regulated) were found.",
    )
    REDACT_APPLIED = (
        "llm.redact.applied",
        "A RedactionPolicy was applied; sensitive values replaced.",
    )

    # ------------------------------------------------------------------
    # llm.diff.*  — RFC-0001 §7.2
    # ------------------------------------------------------------------
    DIFF_COMPUTED = (
        "llm.diff.computed",
        "A textual or semantic diff was computed between two events.",
    )
    DIFF_REGRESSION_FLAGGED = (
        "llm.diff.regression.flagged",
        "A diff computation exceeded the regression similarity threshold.",
    )

    # ------------------------------------------------------------------
    # llm.template.*  — RFC-0001 §7.2
    # ------------------------------------------------------------------
    TEMPLATE_REGISTERED = (
        "llm.template.registered",
        "A new template or version was added to the registry.",
    )
    TEMPLATE_VARIABLE_BOUND = (
        "llm.template.variable.bound",
        "A variable was bound to a template for a specific rendering.",
    )
    TEMPLATE_VALIDATION_FAILED = (
        "llm.template.validation.failed",
        "A template could not be loaded or rendered due to validation errors.",
    )

    # ------------------------------------------------------------------
    # llm.audit.*  — RFC-0001 §11
    # ------------------------------------------------------------------
    AUDIT_KEY_ROTATED = (
        "llm.audit.key.rotated",
        "The HMAC signing key was rotated (RFC-0001 §11.5).",
    )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def namespace(self) -> str:
        """Return the ``llm.<ns>`` namespace prefix (e.g. ``"llm.trace"``)."""
        parts = self.value.split(".")
        return f"{parts[0]}.{parts[1]}"

    @property
    def description(self) -> str:
        """Return the one-line RFC description for this event type."""
        return self._description


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_REGISTERED: Final[frozenset[str]] = frozenset(et.value for et in EventType)


def is_registered(event_type: str) -> bool:
    """Return ``True`` if *event_type* is a first-party registered type (RFC Appendix B)."""
    return event_type in _REGISTERED


def namespace_of(event_type: str) -> str:
    """Extract the ``llm.<ns>`` namespace prefix from *event_type*.

    Works for both registered RFC types and extension types.

    Raises:
        EventTypeError: If *event_type* does not match the expected pattern.

    Example::

        namespace_of("llm.trace.span.completed")        # "llm.trace"
        namespace_of("com.example.myns.event.action")  # "com.example"
    """
    if not _EVENT_TYPE_RE.match(event_type):
        raise EventTypeError(
            event_type,
            f"does not match required pattern {EVENT_TYPE_PATTERN!r}",
        )
    parts = event_type.split(".")
    return f"{parts[0]}.{parts[1]}"


def validate_custom(event_type: str) -> None:
    """Validate a third-party extension event type string (RFC-0001 §7.3).

    Extension types MUST use a reverse-domain prefix (e.g. ``com.example.…``)
    and MUST NOT claim a reserved ``llm.*`` namespace.

    Raises:
        EventTypeError: If the type is malformed or claims a reserved namespace.

    Example::

        validate_custom("com.example.model.call.completed")   # OK
        validate_custom("llm.trace.span.completed")           # raises — reserved
    """
    if not _EVENT_TYPE_RE.match(event_type):
        raise EventTypeError(
            event_type,
            f"does not match the required pattern {EVENT_TYPE_PATTERN!r}. "
            "Extension types must use a reverse-domain prefix outside 'llm.*'.",
        )

    ns = namespace_of(event_type)
    if ns in _RESERVED_NAMESPACES and not is_registered(event_type):
        raise EventTypeError(
            event_type,
            f"namespace '{ns}' is reserved by RFC-0001. "
            "Use a reverse-domain prefix (e.g. 'com.example.…') for custom types.",
        )
    if ns in _FUTURE_NAMESPACES:
        raise EventTypeError(
            event_type,
            f"namespace '{ns}' is reserved for future AGENTOBS standardisation (RFC-0001 §7.4).",
        )


def get_by_value(value: str) -> EventType | None:
    """Return the :class:`EventType` matching *value*, or ``None``.

    Example::

        et = get_by_value("llm.trace.span.completed")
        assert et is EventType.TRACE_SPAN_COMPLETED
    """
    try:
        return EventType(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Span error category
# ---------------------------------------------------------------------------

SpanErrorCategory = Literal[
    "agent_error",
    "llm_error",
    "tool_error",
    "timeout_error",
    "unknown_error",
]
"""Valid values for :attr:`~agentobs._span.Span.error_category`.

Automatically set by :meth:`~agentobs._span.Span.record_error` based
on the exception type, or supplied explicitly by the caller.
"""
