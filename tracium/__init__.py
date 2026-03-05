"""Tracium — Python SDK for the AGENTOBS Observability Standard (RFC-0001 v2.0).

Every tool in the LLM Developer Toolkit emits events that conform to the
:class:`~tracium.event.Event` envelope defined here.  The schema is
OpenTelemetry-compatible, tamper-evident, and enterprise-grade.

Quick start
-----------
::

    from tracium import Event, EventType, Tags

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
* :class:`~tracium.event.Event`
* :class:`~tracium.event.Tags`
* :data:`~tracium.event.SCHEMA_VERSION`

Event types
~~~~~~~~~~~
* :class:`~tracium.types.EventType` â€” RFC Appendix B canonical types
* :func:`~tracium.types.is_registered`
* :func:`~tracium.types.namespace_of`
* :func:`~tracium.types.validate_custom`
* :func:`~tracium.types.get_by_value`

ULID
~~~~
* :func:`~tracium.ulid.generate`
* :func:`~tracium.ulid.validate`
* :func:`~tracium.ulid.extract_timestamp_ms`

PII redaction (RFC Â§12)
~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~tracium.redact.Sensitivity`
* :class:`~tracium.redact.Redactable`
* :class:`~tracium.redact.RedactionPolicy`
* :class:`~tracium.redact.RedactionResult`
* :class:`~tracium.redact.PIINotRedactedError`
* :func:`~tracium.redact.contains_pii`
* :func:`~tracium.redact.assert_redacted`

HMAC signing & audit chain (RFC Â§11)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* :func:`~tracium.signing.sign`
* :func:`~tracium.signing.verify`
* :func:`~tracium.signing.verify_chain`
* :func:`~tracium.signing.assert_verified`
* :class:`~tracium.signing.ChainVerificationResult`
* :class:`~tracium.signing.AuditStream`

Export backends (RFC Â§14)
~~~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~tracium.export.otlp.OTLPExporter`
* :class:`~tracium.export.otlp.ResourceAttributes`
* :class:`~tracium.export.webhook.WebhookExporter`
* :class:`~tracium.export.jsonl.JSONLExporter`

Event routing (RFC Â§14)
~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~tracium.stream.EventStream`
* :class:`~tracium.stream.Exporter`
* :func:`~tracium.stream.iter_file`
* :func:`~tracium.stream.aiter_file`

Governance (RFC Â§13)
~~~~~~~~~~~~~~~~~~~~~
* :class:`~tracium.governance.EventGovernancePolicy`
* :class:`~tracium.governance.GovernanceViolationError`
* :class:`~tracium.governance.GovernanceWarning`

Consumer registration (RFC Â§16)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* :class:`~tracium.consumer.ConsumerRecord`
* :class:`~tracium.consumer.ConsumerRegistry`
* :class:`~tracium.consumer.IncompatibleSchemaError`
* :func:`~tracium.consumer.register_consumer`
* :func:`~tracium.consumer.assert_compatible`

Validation
~~~~~~~~~~
* :func:`~tracium.validate.validate_event`

Exceptions
~~~~~~~~~~
* :class:`~tracium.exceptions.LLMSchemaError`
* :class:`~tracium.exceptions.SchemaValidationError`
* :class:`~tracium.exceptions.SchemaVersionError`
* :class:`~tracium.exceptions.ULIDError`
* :class:`~tracium.exceptions.SerializationError`
* :class:`~tracium.exceptions.DeserializationError`
* :class:`~tracium.exceptions.EventTypeError`
* :class:`~tracium.exceptions.SigningError`
* :class:`~tracium.exceptions.VerificationError`
* :class:`~tracium.exceptions.ExportError`

Version history
---------------
v2.0 â€” RFC-0001 AGENTOBS v2.0 SDK baseline.  Canonical 36-type EventType
        registry (Appendix B), v2.0 envelope (SCHEMA_VERSION="2.0"),
        microsecond-precision timestamp mandate, RFC Â§6.3 ULID first-char
        constraint, source pattern allowing mixed-case, SchemaVersionError,
        11 namespace payload modules (RFC Â§8â€“Â§10), audit chain helpers.
"""

from __future__ import annotations

from tracium._span import (
    AgentRunContext,
    AgentRunContextManager,
    AgentStepContext,
    AgentStepContextManager,
    Span,
    SpanContextManager,
)

# ---------------------------------------------------------------------------
# Phase 2: Core tracer + span
# ---------------------------------------------------------------------------
from tracium._tracer import Tracer, tracer
from tracium.actor import ActorContext

# ---------------------------------------------------------------------------
# Phase 1: Configuration layer
# ---------------------------------------------------------------------------
from tracium.config import TraciumConfig, configure, get_config
from tracium.consumer import (
    ConsumerRecord,
    ConsumerRegistry,
    IncompatibleSchemaError,
    assert_compatible,
    register_consumer,
)
from tracium.consumer import (
    get_registry as get_consumer_registry,
)
from tracium.deprecations import (
    DeprecationNotice,
    DeprecationRegistry,
    get_deprecation_notice,
    list_deprecated,
    mark_deprecated,
    warn_if_deprecated,
)
from tracium.deprecations import (
    get_registry as get_deprecation_registry,
)
from tracium.event import SCHEMA_VERSION, Event, Tags
from tracium.exceptions import (
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
from tracium.export import (
    JSONLExporter,
    OTLPExporter,
    ResourceAttributes,
    WebhookExporter,
)
from tracium.governance import (
    EventGovernancePolicy,
    GovernanceViolationError,
    GovernanceWarning,
    get_global_policy,
    set_global_policy,
)
from tracium.governance import (
    check_event as governance_check_event,
)
from tracium.migrate import (
    DeprecationRecord,
    MigrationResult,
    SunsetPolicy,
    v1_to_v2,
    v2_migration_roadmap,
)

# ---------------------------------------------------------------------------
# Namespace payload dataclasses (RFC §8-§10, §11 audit)
# ---------------------------------------------------------------------------
from tracium.namespaces.audit import (
    AuditChainTamperedPayload,
    AuditChainVerifiedPayload,
    AuditKeyRotatedPayload,
)
from tracium.namespaces.cache import (
    CacheEvictedPayload,
    CacheHitPayload,
    CacheMissPayload,
    CacheWrittenPayload,
)
from tracium.namespaces.cost import (
    CostAttributedPayload,
    CostSessionRecordedPayload,
    CostTokenRecordedPayload,
)
from tracium.namespaces.diff import (
    DiffComputedPayload,
    DiffRegressionFlaggedPayload,
)
from tracium.namespaces.eval_ import (
    EvalRegressionDetectedPayload,
    EvalScenarioCompletedPayload,
    EvalScenarioStartedPayload,
    EvalScoreRecordedPayload,
)
from tracium.namespaces.fence import (
    FenceMaxRetriesExceededPayload,
    FenceRetryTriggeredPayload,
    FenceValidatedPayload,
)
from tracium.namespaces.guard import GuardPayload
from tracium.namespaces.prompt import (
    PromptRenderedPayload,
    PromptTemplateLoadedPayload,
    PromptVersionChangedPayload,
)
from tracium.namespaces.redact import (
    RedactAppliedPayload,
    RedactPhiDetectedPayload,
    RedactPiiDetectedPayload,
)
from tracium.namespaces.template import (
    TemplateRegisteredPayload,
    TemplateValidationFailedPayload,
    TemplateVariableBoundPayload,
)
from tracium.namespaces.trace import (
    AgentRunPayload,
    AgentStepPayload,
    CostBreakdown,
    DecisionPoint,
    GenAIOperationName,
    GenAISystem,
    ModelInfo,
    PricingTier,
    ReasoningStep,
    SpanKind,
    SpanPayload,
    TokenUsage,
    ToolCall,
)
from tracium.redact import (
    PII_TYPES,
    PIINotRedactedError,
    Redactable,
    RedactionPolicy,
    RedactionResult,
    Sensitivity,
    assert_redacted,
    contains_pii,
)
from tracium.signing import (
    AuditStream,
    ChainVerificationResult,
    assert_verified,
    sign,
    verify,
    verify_chain,
)
from tracium.stream import EventStream, Exporter, aiter_file, iter_file
from tracium.types import (
    EventType,
    get_by_value,
    is_registered,
    namespace_of,
    validate_custom,
)
from tracium.ulid import extract_timestamp_ms
from tracium.ulid import generate as generate_ulid
from tracium.ulid import validate as validate_ulid
from tracium.validate import validate_event

__version__: str = "1.0.3"

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
    # Phase 2 — Tracer + Span
    "Tracer",
    # Phase 1 — Configuration
    "TraciumConfig",
    "ULIDError",
    "VerificationError",
    "WebhookExporter",
    # Metadata
    "__version__",
    "aiter_file",
    "assert_compatible",
    "assert_redacted",
    "assert_verified",
    "configure",
    "contains_pii",
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
    "tracer",
    "v1_to_v2",
    "v2_migration_roadmap",
    "validate_custom",
    # Validation
    "validate_event",
    "validate_ulid",
    "verify",
    "verify_chain",
    "warn_if_deprecated",
]

