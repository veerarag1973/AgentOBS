"""AgentOBS — Python SDK for the AGENTOBS Observability Standard (RFC-0001 v2.0).

Every tool in the LLM Developer Toolkit emits events that conform to the
:class:`~agentobs.event.Event` envelope defined here.  The schema is
OpenTelemetry-compatible, tamper-evident, and enterprise-grade.

Quick start
-----------
::

    from agentobs import Event, EventType, Tags

    event = Event(
        event_type=EventType.TRACE_SPAN_COMPLETED,
        source="my-agent@1.0.0",
        payload={"span_name": "run_agent", "status": "ok"},
        tags=Tags(env="production", model="gpt-4o"),
    )
    event.validate()
    print(event.to_json())

Public API
----------
Core envelope
~~~~~~~~~~~~~
* :class:`~agentobs.event.Event`
* :class:`~agentobs.event.Tags`
* :data:`~agentobs.event.SCHEMA_VERSION`

Event types
~~~~~~~~~~~
* :class:`~agentobs.types.EventType` â€” RFC Appendix B canonical types
* :func:`~agentobs.types.is_registered`
* :func:`~agentobs.types.namespace_of`
* :func:`~agentobs.types.validate_custom`
* :func:`~agentobs.types.get_by_value`

ULID
~~~~
* :func:`~agentobs.ulid.generate`
* :func:`~agentobs.ulid.validate`
* :func:`~agentobs.ulid.extract_timestamp_ms`

PII redaction (RFC Â§12)
~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~agentobs.redact.Sensitivity`
* :class:`~agentobs.redact.Redactable`
* :class:`~agentobs.redact.RedactionPolicy`
* :class:`~agentobs.redact.RedactionResult`
* :class:`~agentobs.redact.PIINotRedactedError`
* :func:`~agentobs.redact.contains_pii`
* :func:`~agentobs.redact.assert_redacted`

HMAC signing & audit chain (RFC Â§11)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* :func:`~agentobs.signing.sign`
* :func:`~agentobs.signing.verify`
* :func:`~agentobs.signing.verify_chain`
* :func:`~agentobs.signing.assert_verified`
* :class:`~agentobs.signing.ChainVerificationResult`
* :class:`~agentobs.signing.AuditStream`

Export backends (RFC Â§14)
~~~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~agentobs.export.otlp.OTLPExporter`
* :class:`~agentobs.export.otlp.ResourceAttributes`
* :class:`~agentobs.export.webhook.WebhookExporter`
* :class:`~agentobs.export.jsonl.JSONLExporter`

Event routing (RFC Â§14)
~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~agentobs.stream.EventStream`
* :class:`~agentobs.stream.Exporter`
* :func:`~agentobs.stream.iter_file`
* :func:`~agentobs.stream.aiter_file`

Observability spans & tracing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~agentobs._span.SpanEvent`
* :data:`~agentobs.types.SpanErrorCategory`

Debug utilities
~~~~~~~~~~~~~~~
* :func:`~agentobs.debug.print_tree`
* :func:`~agentobs.debug.summary`
* :func:`~agentobs.debug.visualize`

Governance (RFC Â§13)
~~~~~~~~~~~~~~~~~~~~~
* :class:`~agentobs.governance.EventGovernancePolicy`
* :class:`~agentobs.governance.GovernanceViolationError`
* :class:`~agentobs.governance.GovernanceWarning`

Consumer registration (RFC Â§16)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~agentobs.consumer.ConsumerRecord`
* :class:`~agentobs.consumer.ConsumerRegistry`
* :class:`~agentobs.consumer.IncompatibleSchemaError`
* :func:`~agentobs.consumer.register_consumer`
* :func:`~agentobs.consumer.assert_compatible`

Validation
~~~~~~~~~~
* :func:`~agentobs.validate.validate_event`

Exceptions
~~~~~~~~~~
* :class:`~agentobs.exceptions.LLMSchemaError`
* :class:`~agentobs.exceptions.SchemaValidationError`
* :class:`~agentobs.exceptions.SchemaVersionError`
* :class:`~agentobs.exceptions.ULIDError`
* :class:`~agentobs.exceptions.SerializationError`
* :class:`~agentobs.exceptions.DeserializationError`
* :class:`~agentobs.exceptions.EventTypeError`
* :class:`~agentobs.exceptions.SigningError`
* :class:`~agentobs.exceptions.VerificationError`
* :class:`~agentobs.exceptions.ExportError`

Version history
---------------
v2.0 â€” RFC-0001 AGENTOBS v2.0 SDK baseline.  Canonical 36-type EventType
        registry (Appendix B), v2.0 envelope (SCHEMA_VERSION="2.0"),
        microsecond-precision timestamp mandate, RFC Â§6.3 ULID first-char
        constraint, source pattern allowing mixed-case, SchemaVersionError,
        11 namespace payload modules (RFC Â§8â€“Â§10), audit chain helpers.
"""

from __future__ import annotations

from agentobs.debug import print_tree, summary, visualize
from agentobs._span import (
    AgentRunContext,
    AgentRunContextManager,
    AgentStepContext,
    AgentStepContextManager,
    Span,
    SpanContextManager,
    copy_context,
)

# ---------------------------------------------------------------------------
# Phase 1: Trace object and start_trace()
# ---------------------------------------------------------------------------
from agentobs._trace import Trace, start_trace

# ---------------------------------------------------------------------------
# Phase 4: Metrics extraction + in-process trace store
# ---------------------------------------------------------------------------
import agentobs.metrics as metrics
from agentobs._store import (
    TraceStore,
    get_last_agent_run,
    get_store,
    get_trace,
    list_llm_calls,
    list_tool_calls,
    trace_store,
)

# ---------------------------------------------------------------------------
# Phase 5: Hook registry
# ---------------------------------------------------------------------------
from agentobs._hooks import AsyncHookFn, HookRegistry, hooks

# ---------------------------------------------------------------------------
# Phase 2: Core tracer + span
# ---------------------------------------------------------------------------
from agentobs._tracer import Tracer, tracer
from agentobs.actor import ActorContext

# ---------------------------------------------------------------------------
# Phase 1: Configuration layer
# ---------------------------------------------------------------------------
from agentobs.config import AgentOBSConfig, configure, get_config
from agentobs.consumer import (
    ConsumerRecord,
    ConsumerRegistry,
    IncompatibleSchemaError,
    assert_compatible,
    register_consumer,
)
from agentobs.consumer import (
    get_registry as get_consumer_registry,
)
from agentobs.deprecations import (
    DeprecationNotice,
    DeprecationRegistry,
    get_deprecation_notice,
    list_deprecated,
    mark_deprecated,
    warn_if_deprecated,
)
from agentobs.deprecations import (
    get_registry as get_deprecation_registry,
)
from agentobs.event import SCHEMA_VERSION, Event, Tags
from agentobs.exceptions import (
    DeserializationError,
    EventTypeError,
    ExportError,
    LLMSchemaError,
    SchemaValidationError,
    SchemaVersionError,
    SerializationError,
    SigningError,
    ULIDError,
    VerificationError,
)
from agentobs.export import (
    JSONLExporter,
    OTLPExporter,
    ResourceAttributes,
    WebhookExporter,
)
from agentobs.governance import (
    EventGovernancePolicy,
    GovernanceViolationError,
    GovernanceWarning,
    get_global_policy,
    set_global_policy,
)
from agentobs.governance import (
    check_event as governance_check_event,
)
from agentobs.migrate import (
    DeprecationRecord,
    MigrationResult,
    NotImplementedWarning,
    SunsetPolicy,
    assert_no_sunset_reached,
    v2_migration_roadmap,
)

# ---------------------------------------------------------------------------
# Namespace payload dataclasses (RFC §8-§10, §11 audit)
# ---------------------------------------------------------------------------
from agentobs.namespaces.audit import (
    AuditChainTamperedPayload,
    AuditChainVerifiedPayload,
    AuditKeyRotatedPayload,
)
from agentobs.namespaces.cache import (
    CacheEvictedPayload,
    CacheHitPayload,
    CacheMissPayload,
    CacheWrittenPayload,
)
from agentobs.namespaces.cost import (
    CostAttributedPayload,
    CostSessionRecordedPayload,
    CostTokenRecordedPayload,
)
from agentobs.namespaces.diff import (
    DiffComputedPayload,
    DiffRegressionFlaggedPayload,
)
from agentobs.namespaces.eval_ import (
    EvalRegressionDetectedPayload,
    EvalScenarioCompletedPayload,
    EvalScenarioStartedPayload,
    EvalScoreRecordedPayload,
)
from agentobs.namespaces.fence import (
    FenceMaxRetriesExceededPayload,
    FenceRetryTriggeredPayload,
    FenceValidatedPayload,
)
from agentobs.namespaces.guard import GuardPayload
from agentobs.namespaces.prompt import (
    PromptRenderedPayload,
    PromptTemplateLoadedPayload,
    PromptVersionChangedPayload,
)
from agentobs.namespaces.redact import (
    RedactAppliedPayload,
    RedactPhiDetectedPayload,
    RedactPiiDetectedPayload,
)
from agentobs.namespaces.template import (
    TemplateRegisteredPayload,
    TemplateValidationFailedPayload,
    TemplateVariableBoundPayload,
)
from agentobs.namespaces.trace import (
    AgentRunPayload,
    AgentStepPayload,
    CostBreakdown,
    DecisionPoint,
    GenAIOperationName,
    GenAISystem,
    ModelInfo,
    PricingTier,
    ReasoningStep,
    SpanEvent,
    SpanKind,
    SpanPayload,
    TokenUsage,
    ToolCall,
)
from agentobs.redact import (
    PII_TYPES,
    PIINotRedactedError,
    Redactable,
    RedactionPolicy,
    RedactionResult,
    Sensitivity,
    assert_redacted,
    contains_pii,
)
from agentobs.signing import (
    AuditStream,
    ChainVerificationResult,
    assert_verified,
    sign,
    verify,
    verify_chain,
)
from agentobs.stream import EventStream, Exporter, aiter_file, iter_file
from agentobs.types import (
    EventType,
    SpanErrorCategory,
    get_by_value,
    is_registered,
    namespace_of,
    validate_custom,
)
from agentobs.ulid import extract_timestamp_ms
from agentobs.ulid import generate as generate_ulid
from agentobs.ulid import validate as validate_ulid
from agentobs.validate import validate_event
from agentobs.normalizer import GenericNormalizer, ProviderNormalizer
from agentobs.trace import trace
from agentobs.export.otlp_bridge import SpanOTLPBridge, span_to_otlp_dict
from agentobs.cost import CostTracker, BudgetMonitor, budget_alert, emit_cost_event, emit_cost_attributed, cost_summary, CostRecord
from agentobs.inspect import InspectorSession, ToolCallRecord, inspect_trace
from agentobs.toolsmith import (
    tool,
    ToolRegistry,
    ToolSchema,
    ToolParameter,
    ToolValidationError,
    build_openai_schema,
    build_anthropic_schema,
    default_registry,
)
from agentobs.retry import (
    retry,
    FallbackChain,
    CircuitBreaker,
    CircuitState,
    CostAwareRouter,
    AllProvidersFailedError,
    CircuitOpenError,
)
from agentobs.cache import (
    SemanticCache,
    cached,
    InMemoryBackend,
    SQLiteBackend,
    RedisBackend,
    CacheBackendError,
    CacheEntry,
)
from agentobs.lint import LintError, run_checks

__version__: str = "1.0.8"
#: RFC-0001 conformance profile label (AGENTOBS-Enterprise-2.0).
from typing import Final as _Final
CONFORMANCE_PROFILE: _Final[str] = "AGENTOBS-Enterprise-2.0"

# Optional sub-modules — import on demand to keep startup cost zero.
import agentobs.testing as testing  # noqa: E402
import agentobs.auto as auto  # noqa: E402

__all__: list[str] = [
    "PII_TYPES",
    "SCHEMA_VERSION",
    # Actor identity context
    "ActorContext",
    "AgentRunContext",
    "AgentRunContextManager",
    "AgentRunPayload",
    "AgentStepContext",
    "AgentStepContextManager",
    "AgentStepPayload",
    "AuditChainTamperedPayload",
    "AuditChainVerifiedPayload",
    # audit
    "AuditKeyRotatedPayload",
    "AuditStream",
    "CacheEvictedPayload",
    # cache
    "CacheHitPayload",
    "CacheMissPayload",
    "CacheWrittenPayload",
    "ChainVerificationResult",
    # Consumer registration (RFC Â§16)
    "ConsumerRecord",
    "ConsumerRegistry",
    "CostAttributedPayload",
    "CostBreakdown",
    "CostSessionRecordedPayload",
    # cost
    "CostTokenRecordedPayload",
    "DecisionPoint",
    # Deprecation registry
    "DeprecationNotice",
    "DeprecationRecord",
    "DeprecationRegistry",
    "DeserializationError",
    # diff
    "DiffComputedPayload",
    "DiffRegressionFlaggedPayload",
    "EvalRegressionDetectedPayload",
    "EvalScenarioCompletedPayload",
    "EvalScenarioStartedPayload",
    # eval
    "EvalScoreRecordedPayload",
    # Core envelope
    "Event",
    # Schema governance (RFC Â§13)
    "EventGovernancePolicy",
    # Event routing (RFC Â§14)
    "EventStream",
    # Event types
    "EventType",
    "EventTypeError",
    "ExportError",
    "Exporter",
    "FenceMaxRetriesExceededPayload",
    "FenceRetryTriggeredPayload",
    # fence
    "FenceValidatedPayload",
    "GenAIOperationName",
    # Namespace payload dataclasses (RFC §8-§11)
    # trace — value objects
    "GenAISystem",
    "GovernanceViolationError",
    "GovernanceWarning",
    # guard
    "GuardPayload",
    "IncompatibleSchemaError",
    "JSONLExporter",
    # Exceptions
    "LLMSchemaError",
    # Migration scaffold (v1→v2)
    "MigrationResult",
    "NotImplementedWarning",
    "assert_no_sunset_reached",
    "ModelInfo",
    # Export backends (RFC Â§14)
    "OTLPExporter",
    "PIINotRedactedError",
    "PricingTier",
    # prompt
    "PromptRenderedPayload",
    "PromptTemplateLoadedPayload",
    "PromptVersionChangedPayload",
    "ReasoningStep",
    "RedactAppliedPayload",
    "RedactPhiDetectedPayload",
    # redact
    "RedactPiiDetectedPayload",
    "Redactable",
    "RedactionPolicy",
    "RedactionResult",
    "ResourceAttributes",
    "SchemaValidationError",
    "SchemaVersionError",
    # PII Redaction (RFC Â§12)
    "Sensitivity",
    "SerializationError",
    "SigningError",
    "Span",
    "SpanContextManager",
    "SpanErrorCategory",
    "SpanEvent",
    "SpanKind",
    # trace — payloads
    "SpanPayload",
    "SunsetPolicy",
    "Tags",
    # template
    "TemplateRegisteredPayload",
    "TemplateValidationFailedPayload",
    "TemplateVariableBoundPayload",
    "TokenUsage",
    "ToolCall",
    # Phase 3 — Debug utilities
    "print_tree",
    "summary",
    "visualize",
    # Phase 1 — Trace object
    "Trace",
    # Phase 2 — Tracer + Span
    "Tracer",
    # Phase 4 — Metrics + trace store
    "metrics",
    "TraceStore",
    "get_store",
    "get_trace",
    "get_last_agent_run",
    "list_tool_calls",
    "list_llm_calls",
    "trace_store",
    # Phase 5 — Hooks
    "AsyncHookFn",
    "HookRegistry",
    "hooks",
    # Phase 1 — Configuration
    "AgentOBSConfig",
    "ULIDError",
    "VerificationError",
    "WebhookExporter",
    # Metadata
    "__version__",
    "testing",
    "auto",
    "aiter_file",
    "assert_compatible",
    "assert_redacted",
    "assert_verified",
    "configure",
    "contains_pii",
    # Context propagation helper (Phase 1)
    "copy_context",
    "extract_timestamp_ms",
    # ULID
    "generate_ulid",
    "get_by_value",
    "get_config",
    "get_consumer_registry",
    "get_deprecation_notice",
    "get_deprecation_registry",
    "get_global_policy",
    "governance_check_event",
    "is_registered",
    "iter_file",
    "list_deprecated",
    "mark_deprecated",
    "namespace_of",
    "register_consumer",
    "set_global_policy",
    # HMAC Signing & Audit Chain (RFC Â§11)
    "sign",
    "start_trace",
    "tracer",
    "v2_migration_roadmap",
    "validate_custom",
    # Validation
    "validate_event",
    "validate_ulid",
    "verify",
    "verify_chain",
    "warn_if_deprecated",
    # Normalizer (RFC-0001 §10.4)
    "ProviderNormalizer",
    "GenericNormalizer",
    # Conformance
    "CONFORMANCE_PROFILE",
    # Tool 1 — @trace() decorator + OTLP bridge
    "trace",
    "SpanOTLPBridge",
    "span_to_otlp_dict",
    # Tool 2 — Cost Calculation Engine
    "BudgetMonitor",
    "CostRecord",
    "CostTracker",
    "budget_alert",
    "cost_summary",
    "emit_cost_attributed",
    "emit_cost_event",
    # Tool 3 — Tool Call Inspector
    "InspectorSession",
    "ToolCallRecord",
    "inspect_trace",
    # Tool 4 — Tool Schema Builder
    "tool",
    "ToolRegistry",
    "ToolSchema",
    "ToolParameter",
    "ToolValidationError",
    "build_openai_schema",
    "build_anthropic_schema",
    "default_registry",
    # Tool 5 — Retry and Fallback Engine
    "retry",
    "FallbackChain",
    "CircuitBreaker",
    "CircuitState",
    "CostAwareRouter",
    "AllProvidersFailedError",
    "CircuitOpenError",
    # Tool 6 — Semantic Cache Engine
    "SemanticCache",
    "cached",
    "InMemoryBackend",
    "SQLiteBackend",
    "RedisBackend",
    "CacheBackendError",
    "CacheEntry",
    # Tool 7 — SDK Instrumentation Linter
    "LintError",
    "run_checks",
]

