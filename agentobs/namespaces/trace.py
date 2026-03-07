"""agentobs.namespaces.trace — Span and agent payload types (RFC-0001 §8).

This module provides Python dataclasses for the ``llm.trace.*`` namespace.
All types map directly to the JSON Schema defined in
``docs/schema/payloads/span.schema.json``,
``docs/schema/payloads/agent-step.schema.json``, and
``docs/schema/payloads/agent-run.schema.json``.

Shared value objects (``TokenUsage``, ``ModelInfo``, ``CostBreakdown``,
``PricingTier``, ``ToolCall``, ``ReasoningStep``, ``DecisionPoint``) are
defined here because the trace namespace is where they are first introduced
and other namespaces reference them.

Enumerations
------------
GenAISystem
    RFC §10.1 — normalised provider identifier (OTel ``gen_ai.system``).
GenAIOperationName
    RFC §10.2 — type of LLM operation (OTel ``gen_ai.operation.name``).
SpanKind
    RFC §10.3 — OTel SpanKind values relevant to LLM operations.

Value objects
-------------
TokenUsage
    RFC §9.1 — token counts with OTel-aligned field names.
ModelInfo
    RFC §9.2 — model identity and provider.
CostBreakdown
    RFC §9.3 — typed cost attribution record.
PricingTier
    RFC §9.4 — pricing rates snapshot for cost reproduction.
ToolCall
    RFC §8.1 — single tool invocation within a span.
ReasoningStep
    RFC §8.2 — chain-of-thought unit; raw content MUST NOT be stored.
DecisionPoint
    RFC §8.3 — explicit branching decision made by an agent.

Payload dataclasses
-------------------
SpanPayload
    RFC §8.1 — single unit of LLM work (model call, tool, agent invocation).
AgentStepPayload
    RFC §8.4 — one iteration of a multi-step agent loop.
AgentRunPayload
    RFC §8.5 — root summary for a complete agent run.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "AgentRunPayload",
    "AgentStepPayload",
    "CostBreakdown",
    "DecisionPoint",
    "GenAIOperationName",
    # Enumerations
    "GenAISystem",
    "ModelInfo",
    "PricingTier",
    "ReasoningStep",
    "SpanEvent",
    "SpanKind",
    # Payloads
    "SpanPayload",
    # Value objects
    "TokenUsage",
    "ToolCall",
]

# ---------------------------------------------------------------------------
# Compiled validation patterns (module-level, reused across instances)
# ---------------------------------------------------------------------------
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


# ---------------------------------------------------------------------------
# Enumerations (RFC §10)
# ---------------------------------------------------------------------------

class GenAISystem(str, Enum):
    """RFC-0001 §10.1 — LLM provider identifier.

    Maps directly to OTel ``gen_ai.system`` semantic convention values.
    Use ``CUSTOM`` (``"_custom"``) for private or enterprise deployments;
    MUST set ``ModelInfo.custom_system_name`` when ``CUSTOM`` is used.
    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    COHERE = "cohere"
    VERTEX_AI = "vertex_ai"
    AWS_BEDROCK = "aws_bedrock"
    AZ_AI_INFERENCE = "az.ai.inference"
    GROQ = "groq"
    OLLAMA = "ollama"
    MISTRAL_AI = "mistral_ai"
    TOGETHER_AI = "together_ai"
    HUGGING_FACE = "hugging_face"
    CUSTOM = "_custom"


class GenAIOperationName(str, Enum):
    """RFC-0001 §10.2 — Type of LLM operation performed.

    Maps to OTel ``gen_ai.operation.name``.
    """

    CHAT = "chat"
    TEXT_COMPLETION = "text_completion"
    EMBEDDINGS = "embeddings"
    IMAGE_GENERATION = "image_generation"
    EXECUTE_TOOL = "execute_tool"
    INVOKE_AGENT = "invoke_agent"
    CREATE_AGENT = "create_agent"
    REASONING = "reasoning"


class SpanKind(str, Enum):
    """RFC-0001 §10.3 — OTel SpanKind for LLM operations."""

    CLIENT = "CLIENT"      # Outbound LLM API call — most common
    SERVER = "SERVER"      # Incoming agent request
    INTERNAL = "INTERNAL"  # Internal reasoning or routing step
    CONSUMER = "CONSUMER"  # Tool execution triggered by LLM output
    PRODUCER = "PRODUCER"  # Event emitted by an agent for downstream consumption


# ---------------------------------------------------------------------------
# Value objects (RFC §9, §8.1-§8.3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenUsage:
    """RFC-0001 §9.1 — Token consumption record for a model call.

    Uses OTel-aligned names: ``input_tokens`` / ``output_tokens`` (not
    ``prompt_tokens`` / ``completion_tokens``).
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None
    image_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            v = getattr(self, name)
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"TokenUsage.{name} must be a non-negative int")
        for opt in ("cached_tokens", "cache_creation_tokens", "reasoning_tokens", "image_tokens"):
            v = getattr(self, opt)
            if v is not None and (not isinstance(v, int) or v < 0):
                raise ValueError(f"TokenUsage.{opt} must be a non-negative int or None")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        d: dict[str, Any] = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }
        for name in ("cached_tokens", "cache_creation_tokens", "reasoning_tokens", "image_tokens"):
            v = getattr(self, name)
            if v is not None:
                d[name] = v
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenUsage:
        """Deserialise from a plain ``dict``."""
        return cls(
            input_tokens=int(data["input_tokens"]),
            output_tokens=int(data["output_tokens"]),
            total_tokens=int(data["total_tokens"]),
            cached_tokens=int(data["cached_tokens"]) if "cached_tokens" in data else None,
            cache_creation_tokens=int(data["cache_creation_tokens"]) if "cache_creation_tokens" in data else None,  # noqa: E501
            reasoning_tokens=int(data["reasoning_tokens"]) if "reasoning_tokens" in data else None,
            image_tokens=int(data["image_tokens"]) if "image_tokens" in data else None,
        )


@dataclass(frozen=True)
class ModelInfo:
    """RFC-0001 §9.2 — Model identity and provider information.

    ``custom_system_name`` is REQUIRED when ``system`` is
    :attr:`GenAISystem.CUSTOM` (``"_custom"``).
    """

    system: GenAISystem | str
    name: str
    response_model: str | None = None
    version: str | None = None
    custom_system_name: str | None = None

    def __post_init__(self) -> None:
        sys_val = self.system.value if isinstance(self.system, GenAISystem) else self.system
        if sys_val == "_custom" and not self.custom_system_name:
            raise ValueError(
                "ModelInfo.custom_system_name is REQUIRED when system is '_custom' (RFC §4 P6)"
            )
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ModelInfo.name must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        sys_val = self.system.value if isinstance(self.system, GenAISystem) else self.system
        d: dict[str, Any] = {"system": sys_val, "name": self.name}
        if self.response_model is not None:
            d["response_model"] = self.response_model
        if self.version is not None:
            d["version"] = self.version
        if self.custom_system_name is not None:
            d["custom_system_name"] = self.custom_system_name
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelInfo:
        """Deserialise from a plain ``dict``."""
        sys_raw = data["system"]
        try:
            system: GenAISystem | str = GenAISystem(sys_raw)
        except ValueError:
            system = sys_raw
        return cls(
            system=system,
            name=data["name"],
            response_model=data.get("response_model"),
            version=data.get("version"),
            custom_system_name=data.get("custom_system_name"),
        )


@dataclass(frozen=True)
class CostBreakdown:
    """RFC-0001 §9.3 — Typed cost attribution record.

    ``total_cost_usd`` MUST equal
    ``input_cost_usd + output_cost_usd + reasoning_cost_usd - cached_discount_usd``
    within ±1e-6 absolute tolerance.

    Use :meth:`zero` to create a zero-filled instance when pricing data
    is unavailable (§9.3 zero-fill allowance).
    """

    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    cached_discount_usd: float = 0.0
    reasoning_cost_usd: float = 0.0
    currency: str = "USD"
    pricing_date: str | None = None

    _TOLERANCE: float = 1e-6

    def __post_init__(self) -> None:
        for name in ("input_cost_usd", "output_cost_usd", "total_cost_usd",
                     "cached_discount_usd", "reasoning_cost_usd"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"CostBreakdown.{name} must be a non-negative number")
        if not _CURRENCY_RE.match(self.currency):
            raise ValueError("CostBreakdown.currency must be a 3-letter ISO 4217 code")
        if self.pricing_date is not None and not _ISO_DATE_RE.match(self.pricing_date):
            raise ValueError("CostBreakdown.pricing_date must be YYYY-MM-DD")
        expected = (
            self.input_cost_usd
            + self.output_cost_usd
            + self.reasoning_cost_usd
            - self.cached_discount_usd
        )
        if abs(self.total_cost_usd - expected) > self._TOLERANCE:
            raise ValueError(
                f"CostBreakdown.total_cost_usd {self.total_cost_usd} != "
                f"input + output + reasoning - cached_discount = {expected:.8f} (±{self._TOLERANCE})"  # noqa: E501
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        d: dict[str, Any] = {
            "input_cost_usd": self.input_cost_usd,
            "output_cost_usd": self.output_cost_usd,
            "total_cost_usd": self.total_cost_usd,
        }
        if self.cached_discount_usd != 0.0:
            d["cached_discount_usd"] = self.cached_discount_usd
        if self.reasoning_cost_usd != 0.0:
            d["reasoning_cost_usd"] = self.reasoning_cost_usd
        if self.currency != "USD":
            d["currency"] = self.currency
        if self.pricing_date is not None:
            d["pricing_date"] = self.pricing_date
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostBreakdown:
        """Deserialise from a plain ``dict``."""
        return cls(
            input_cost_usd=float(data["input_cost_usd"]),
            output_cost_usd=float(data["output_cost_usd"]),
            total_cost_usd=float(data["total_cost_usd"]),
            cached_discount_usd=float(data.get("cached_discount_usd", 0.0)),
            reasoning_cost_usd=float(data.get("reasoning_cost_usd", 0.0)),
            currency=data.get("currency", "USD"),
            pricing_date=data.get("pricing_date"),
        )

    @classmethod
    def zero(cls) -> CostBreakdown:
        """Return a zero-filled CostBreakdown (§9.3 zero-fill allowance)."""
        return cls(input_cost_usd=0.0, output_cost_usd=0.0, total_cost_usd=0.0)


@dataclass(frozen=True)
class PricingTier:
    """RFC-0001 §9.4 — Pricing rates snapshot for cost reproduction.

    Stores the exact pricing rates used to compute a ``CostBreakdown`` so
    cost calculations remain reproducible indefinitely.
    """

    system: GenAISystem | str
    model: str
    input_per_million_usd: float
    output_per_million_usd: float
    effective_date: str
    cached_input_per_million_usd: float | None = None
    reasoning_per_million_usd: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ValueError("PricingTier.model must be a non-empty string")
        if not _ISO_DATE_RE.match(self.effective_date):
            raise ValueError("PricingTier.effective_date must be YYYY-MM-DD")
        for name in ("input_per_million_usd", "output_per_million_usd"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"PricingTier.{name} must be a non-negative number")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        sys_val = self.system.value if isinstance(self.system, GenAISystem) else self.system
        d: dict[str, Any] = {
            "system": sys_val,
            "model": self.model,
            "input_per_million_usd": self.input_per_million_usd,
            "output_per_million_usd": self.output_per_million_usd,
            "effective_date": self.effective_date,
        }
        if self.cached_input_per_million_usd is not None:
            d["cached_input_per_million_usd"] = self.cached_input_per_million_usd
        if self.reasoning_per_million_usd is not None:
            d["reasoning_per_million_usd"] = self.reasoning_per_million_usd
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PricingTier:
        """Deserialise from a plain ``dict``."""
        sys_raw = data["system"]
        try:
            system: GenAISystem | str = GenAISystem(sys_raw)
        except ValueError:
            system = sys_raw
        return cls(
            system=system,
            model=data["model"],
            input_per_million_usd=float(data["input_per_million_usd"]),
            output_per_million_usd=float(data["output_per_million_usd"]),
            effective_date=data["effective_date"],
            cached_input_per_million_usd=float(data["cached_input_per_million_usd"])
            if "cached_input_per_million_usd" in data else None,
            reasoning_per_million_usd=float(data["reasoning_per_million_usd"])
            if "reasoning_per_million_usd" in data else None,
        )


@dataclass
class SpanEvent:
    """A named, timestamped event within a span.

    Unlike spans (which represent durations), events are instantaneous
    points in time recorded inside an enclosing span.

    Attributes:
        name:         Event name (e.g. ``"cache.hit"``, ``"retry.attempt.2"``).
        timestamp_ns: Nanosecond-precision Unix timestamp (auto-filled on creation).
        metadata:     Arbitrary key-value metadata attached to this event.
    """

    name: str
    timestamp_ns: int = field(default_factory=time.time_ns)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("SpanEvent.name must be a non-empty string")
        if self.timestamp_ns < 0:
            raise ValueError("SpanEvent.timestamp_ns must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "name": self.name,
            "timestamp_ns": self.timestamp_ns,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpanEvent:
        """Deserialise from a plain dict."""
        return cls(
            name=data["name"],
            timestamp_ns=int(data["timestamp_ns"]),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ToolCall:
    """RFC-0001 §8.1 — A single tool invocation within a span.

    ``arguments_hash`` stores a SHA-256 hash of the canonical JSON of
    arguments.  Raw argument values SHOULD NOT be stored by default (§20.4);
    set ``AgentOBSConfig.include_raw_tool_io = True`` to opt in.
    """

    tool_call_id: str
    function_name: str
    status: str  # "success" | "error" | "timeout" | "cancelled"
    arguments_hash: str | None = None  # 64 lowercase hex chars, no prefix
    error_type: str | None = None
    duration_ms: float | None = None
    arguments_raw: str | None = None   # populated only when include_raw_tool_io=True
    result_raw: str | None = None      # populated only when include_raw_tool_io=True
    retry_count: int | None = None
    external_api: str | None = None

    _VALID_STATUSES = frozenset({"success", "error", "timeout", "cancelled"})

    def __post_init__(self) -> None:
        if not isinstance(self.tool_call_id, str) or not self.tool_call_id:
            raise ValueError("ToolCall.tool_call_id must be a non-empty string")
        if not isinstance(self.function_name, str) or not self.function_name:
            raise ValueError("ToolCall.function_name must be a non-empty string")
        if self.status not in self._VALID_STATUSES:
            raise ValueError(f"ToolCall.status must be one of {sorted(self._VALID_STATUSES)}")
        if self.arguments_hash is not None and not _SHA256_RE.match(self.arguments_hash):
            raise ValueError("ToolCall.arguments_hash must be 64 lowercase hex chars (SHA-256)")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("ToolCall.duration_ms must be non-negative")
        if self.retry_count is not None and self.retry_count < 0:
            raise ValueError("ToolCall.retry_count must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        d: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "function_name": self.function_name,
            "status": self.status,
        }
        if self.arguments_hash is not None:
            d["arguments_hash"] = self.arguments_hash
        if self.error_type is not None:
            d["error_type"] = self.error_type
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        # arguments_raw / result_raw contain raw PII; only emit when the
        # operator has explicitly enabled raw tool I/O capture.
        from agentobs.config import get_config  # noqa: PLC0415
        if self.arguments_raw is not None and get_config().include_raw_tool_io:
            d["arguments_raw"] = self.arguments_raw
        if self.result_raw is not None and get_config().include_raw_tool_io:
            d["result_raw"] = self.result_raw
        if self.retry_count is not None:
            d["retry_count"] = self.retry_count
        if self.external_api is not None:
            d["external_api"] = self.external_api
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        """Deserialise from a plain ``dict``."""
        return cls(
            tool_call_id=data["tool_call_id"],
            function_name=data["function_name"],
            status=data["status"],
            arguments_hash=data.get("arguments_hash"),
            error_type=data.get("error_type"),
            duration_ms=float(data["duration_ms"]) if "duration_ms" in data else None,
            arguments_raw=data.get("arguments_raw"),
            result_raw=data.get("result_raw"),
            retry_count=int(data["retry_count"]) if "retry_count" in data else None,
            external_api=data.get("external_api"),
        )


@dataclass(frozen=True)
class ReasoningStep:
    """RFC-0001 §8.2 — A discrete chain-of-thought unit.

    **Critical:** Raw reasoning content MUST NOT be stored — only the
    SHA-256 ``content_hash`` (64 lowercase hex chars, no prefix) MAY be
    stored.
    """

    step_index: int
    reasoning_tokens: int
    duration_ms: float | None = None
    content_hash: str | None = None  # 64 lowercase hex chars, no prefix

    def __post_init__(self) -> None:
        if not isinstance(self.step_index, int) or self.step_index < 0:
            raise ValueError("ReasoningStep.step_index must be a non-negative int")
        if not isinstance(self.reasoning_tokens, int) or self.reasoning_tokens < 0:
            raise ValueError("ReasoningStep.reasoning_tokens must be a non-negative int")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("ReasoningStep.duration_ms must be non-negative")
        if self.content_hash is not None and not _SHA256_RE.match(self.content_hash):
            raise ValueError(
                "ReasoningStep.content_hash must be 64 lowercase hex chars (SHA-256, no prefix)"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        d: dict[str, Any] = {
            "step_index": self.step_index,
            "reasoning_tokens": self.reasoning_tokens,
        }
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        if self.content_hash is not None:
            d["content_hash"] = self.content_hash
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReasoningStep:
        """Deserialise from a plain ``dict``."""
        return cls(
            step_index=int(data["step_index"]),
            reasoning_tokens=int(data["reasoning_tokens"]),
            duration_ms=float(data["duration_ms"]) if "duration_ms" in data else None,
            content_hash=data.get("content_hash"),
        )


@dataclass(frozen=True)
class DecisionPoint:
    """RFC-0001 §8.3 — An explicit branching decision recorded during an agent step.

    ``rationale`` is OPTIONAL for black-box models that do not expose reasoning.
    """

    decision_id: str
    decision_type: str  # "tool_selection"|"route_choice"|"loop_termination"|"escalation"
    options_considered: list[str]
    chosen_option: str
    rationale: str | None = None

    _VALID_TYPES = frozenset({"tool_selection", "route_choice", "loop_termination", "escalation"})

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, str) or not self.decision_id:
            raise ValueError("DecisionPoint.decision_id must be a non-empty string")
        if self.decision_type not in self._VALID_TYPES:
            raise ValueError(f"DecisionPoint.decision_type must be one of {sorted(self._VALID_TYPES)}")  # noqa: E501
        if not self.options_considered:
            raise ValueError("DecisionPoint.options_considered must be a non-empty list")
        if not isinstance(self.chosen_option, str) or not self.chosen_option:
            raise ValueError("DecisionPoint.chosen_option must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        d: dict[str, Any] = {
            "decision_id": self.decision_id,
            "decision_type": self.decision_type,
            "options_considered": list(self.options_considered),
            "chosen_option": self.chosen_option,
        }
        if self.rationale is not None:
            d["rationale"] = self.rationale
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionPoint:
        """Deserialise from a plain ``dict``."""
        return cls(
            decision_id=data["decision_id"],
            decision_type=data["decision_type"],
            options_considered=list(data["options_considered"]),
            chosen_option=data["chosen_option"],
            rationale=data.get("rationale"),
        )


# ---------------------------------------------------------------------------
# Payload dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SpanPayload:
    """RFC-0001 §8.1 — A single unit of LLM work.

    Used with event types: ``llm.trace.span.started``,
    ``llm.trace.span.completed``, ``llm.trace.span.failed``,
    ``llm.trace.reasoning.step``.
    """

    span_id: str           # 16 lowercase hex chars
    trace_id: str          # 32 lowercase hex chars
    span_name: str
    operation: GenAIOperationName | str
    span_kind: SpanKind | str
    status: str            # "ok" | "error" | "timeout"
    start_time_unix_nano: int
    end_time_unix_nano: int
    duration_ms: float
    parent_span_id: str | None = None
    agent_run_id: str | None = None
    model: ModelInfo | None = None
    token_usage: TokenUsage | None = None
    cost: CostBreakdown | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_steps: list[ReasoningStep] = field(default_factory=list)
    finish_reason: str | None = None
    error: str | None = None
    error_type: str | None = None
    attributes: dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    error_category: str | None = None
    events: list[SpanEvent] = field(default_factory=list)

    _VALID_STATUSES = frozenset({"ok", "error", "timeout"})

    def __post_init__(self) -> None:
        if not _SPAN_ID_RE.match(self.span_id):
            raise ValueError(f"SpanPayload.span_id must be 16 lowercase hex chars, got {self.span_id!r}")  # noqa: E501
        if not _TRACE_ID_RE.match(self.trace_id):
            raise ValueError(f"SpanPayload.trace_id must be 32 lowercase hex chars, got {self.trace_id!r}")  # noqa: E501
        if not isinstance(self.span_name, str) or not self.span_name:
            raise ValueError("SpanPayload.span_name must be a non-empty string")
        if self.parent_span_id is not None and not _SPAN_ID_RE.match(self.parent_span_id):
            raise ValueError("SpanPayload.parent_span_id must be 16 lowercase hex chars")
        status_val = self.status.value if isinstance(self.status, Enum) else self.status
        if status_val not in self._VALID_STATUSES:
            raise ValueError(f"SpanPayload.status must be one of {sorted(self._VALID_STATUSES)}")
        if self.start_time_unix_nano < 0:
            raise ValueError("SpanPayload.start_time_unix_nano must be non-negative")
        if self.end_time_unix_nano < self.start_time_unix_nano:
            raise ValueError("SpanPayload.end_time_unix_nano must be >= start_time_unix_nano")
        if self.duration_ms < 0:
            raise ValueError("SpanPayload.duration_ms must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        op = self.operation.value if isinstance(self.operation, Enum) else self.operation
        sk = self.span_kind.value if isinstance(self.span_kind, Enum) else self.span_kind
        st = self.status.value if isinstance(self.status, Enum) else self.status
        d: dict[str, Any] = {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "span_name": self.span_name,
            "operation": op,
            "span_kind": sk,
            "status": st,
            "start_time_unix_nano": self.start_time_unix_nano,
            "end_time_unix_nano": self.end_time_unix_nano,
            "duration_ms": self.duration_ms,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "reasoning_steps": [rs.to_dict() for rs in self.reasoning_steps],
        }
        if self.parent_span_id is not None:
            d["parent_span_id"] = self.parent_span_id
        if self.agent_run_id is not None:
            d["agent_run_id"] = self.agent_run_id
        if self.model is not None:
            d["model"] = self.model.to_dict()
        if self.token_usage is not None:
            d["token_usage"] = self.token_usage.to_dict()
        if self.cost is not None:
            d["cost"] = self.cost.to_dict()
        if self.finish_reason is not None:
            d["finish_reason"] = self.finish_reason
        if self.error is not None:
            d["error"] = self.error
        if self.error_type is not None:
            d["error_type"] = self.error_type
        if self.attributes is not None:
            d["attributes"] = self.attributes
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.top_p is not None:
            d["top_p"] = self.top_p
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        if self.error_category is not None:
            d["error_category"] = self.error_category
        if self.events:
            d["events"] = [e.to_dict() for e in self.events]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpanPayload:
        """Deserialise from a plain ``dict``."""
        op_raw = data["operation"]
        try:
            operation: GenAIOperationName | str = GenAIOperationName(op_raw)
        except ValueError:
            operation = op_raw
        sk_raw = data["span_kind"]
        try:
            span_kind: SpanKind | str = SpanKind(sk_raw)
        except ValueError:
            span_kind = sk_raw
        return cls(
            span_id=data["span_id"],
            trace_id=data["trace_id"],
            span_name=data["span_name"],
            operation=operation,
            span_kind=span_kind,
            status=data["status"],
            start_time_unix_nano=int(data["start_time_unix_nano"]),
            end_time_unix_nano=int(data["end_time_unix_nano"]),
            duration_ms=float(data["duration_ms"]),
            parent_span_id=data.get("parent_span_id"),
            agent_run_id=data.get("agent_run_id"),
            model=ModelInfo.from_dict(data["model"]) if "model" in data else None,
            token_usage=TokenUsage.from_dict(data["token_usage"]) if "token_usage" in data else None,  # noqa: E501
            cost=CostBreakdown.from_dict(data["cost"]) if "cost" in data else None,
            tool_calls=[ToolCall.from_dict(tc) for tc in data.get("tool_calls", [])],
            reasoning_steps=[ReasoningStep.from_dict(rs) for rs in data.get("reasoning_steps", [])],
            finish_reason=data.get("finish_reason"),
            error=data.get("error"),
            error_type=data.get("error_type"),
            attributes=data.get("attributes"),
            temperature=float(data["temperature"]) if "temperature" in data else None,
            top_p=float(data["top_p"]) if "top_p" in data else None,
            max_tokens=int(data["max_tokens"]) if "max_tokens" in data else None,
            error_category=data.get("error_category"),
            events=[SpanEvent.from_dict(e) for e in data.get("events", [])],
        )


@dataclass
class AgentStepPayload:
    """RFC-0001 §8.4 — One iteration of a multi-step agent loop.

    Used with event type: ``llm.trace.agent.step``.
    ``step_index`` is zero-based.
    """

    agent_run_id: str
    step_index: int
    span_id: str           # 16 lowercase hex chars
    trace_id: str          # 32 lowercase hex chars
    operation: GenAIOperationName | str
    tool_calls: list[ToolCall]
    reasoning_steps: list[ReasoningStep]
    decision_points: list[DecisionPoint]
    status: str            # "ok" | "error" | "timeout"
    start_time_unix_nano: int
    end_time_unix_nano: int
    duration_ms: float
    parent_span_id: str | None = None
    model: ModelInfo | None = None
    token_usage: TokenUsage | None = None
    cost: CostBreakdown | None = None
    error: str | None = None
    error_type: str | None = None
    step_name: str | None = None

    _VALID_STATUSES = frozenset({"ok", "error", "timeout"})

    def __post_init__(self) -> None:
        if not isinstance(self.agent_run_id, str) or not self.agent_run_id:
            raise ValueError("AgentStepPayload.agent_run_id must be a non-empty string")
        if not isinstance(self.step_index, int) or self.step_index < 0:
            raise ValueError("AgentStepPayload.step_index must be a non-negative int")
        if not _SPAN_ID_RE.match(self.span_id):
            raise ValueError("AgentStepPayload.span_id must be 16 lowercase hex chars")
        if not _TRACE_ID_RE.match(self.trace_id):
            raise ValueError("AgentStepPayload.trace_id must be 32 lowercase hex chars")
        if self.parent_span_id is not None and not _SPAN_ID_RE.match(self.parent_span_id):
            raise ValueError("AgentStepPayload.parent_span_id must be 16 lowercase hex chars")
        status_val = self.status.value if isinstance(self.status, Enum) else self.status
        if status_val not in self._VALID_STATUSES:
            raise ValueError(f"AgentStepPayload.status must be one of {sorted(self._VALID_STATUSES)}")  # noqa: E501
        if self.start_time_unix_nano < 0:
            raise ValueError("AgentStepPayload.start_time_unix_nano must be non-negative")
        if self.end_time_unix_nano < self.start_time_unix_nano:
            raise ValueError("AgentStepPayload.end_time_unix_nano must be >= start_time_unix_nano")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        op = self.operation.value if isinstance(self.operation, Enum) else self.operation
        st = self.status.value if isinstance(self.status, Enum) else self.status
        d: dict[str, Any] = {
            "agent_run_id": self.agent_run_id,
            "step_index": self.step_index,
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "operation": op,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "reasoning_steps": [rs.to_dict() for rs in self.reasoning_steps],
            "decision_points": [dp.to_dict() for dp in self.decision_points],
            "status": st,
            "start_time_unix_nano": self.start_time_unix_nano,
            "end_time_unix_nano": self.end_time_unix_nano,
            "duration_ms": self.duration_ms,
        }
        if self.parent_span_id is not None:
            d["parent_span_id"] = self.parent_span_id
        if self.model is not None:
            d["model"] = self.model.to_dict()
        if self.token_usage is not None:
            d["token_usage"] = self.token_usage.to_dict()
        if self.cost is not None:
            d["cost"] = self.cost.to_dict()
        if self.error is not None:
            d["error"] = self.error
        if self.error_type is not None:
            d["error_type"] = self.error_type
        if self.step_name is not None:
            d["step_name"] = self.step_name
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentStepPayload:
        """Deserialise from a plain ``dict``."""
        op_raw = data["operation"]
        try:
            operation: GenAIOperationName | str = GenAIOperationName(op_raw)
        except ValueError:
            operation = op_raw
        return cls(
            agent_run_id=data["agent_run_id"],
            step_index=int(data["step_index"]),
            span_id=data["span_id"],
            trace_id=data["trace_id"],
            operation=operation,
            tool_calls=[ToolCall.from_dict(tc) for tc in data.get("tool_calls", [])],
            reasoning_steps=[ReasoningStep.from_dict(rs) for rs in data.get("reasoning_steps", [])],
            decision_points=[DecisionPoint.from_dict(dp) for dp in data.get("decision_points", [])],
            status=data["status"],
            start_time_unix_nano=int(data["start_time_unix_nano"]),
            end_time_unix_nano=int(data["end_time_unix_nano"]),
            duration_ms=float(data["duration_ms"]),
            parent_span_id=data.get("parent_span_id"),
            model=ModelInfo.from_dict(data["model"]) if "model" in data else None,
            token_usage=TokenUsage.from_dict(data["token_usage"]) if "token_usage" in data else None,  # noqa: E501
            cost=CostBreakdown.from_dict(data["cost"]) if "cost" in data else None,
            error=data.get("error"),
            error_type=data.get("error_type"),
            step_name=data.get("step_name"),
        )


@dataclass
class AgentRunPayload:
    """RFC-0001 §8.5 — Root-level summary for a complete agent run.

    Used with event type: ``llm.trace.agent.completed``.
    ``agent_run_id`` MUST match across all :class:`AgentStepPayload`
    events for this run.
    """

    agent_run_id: str
    agent_name: str
    trace_id: str          # 32 lowercase hex chars
    root_span_id: str      # 16 lowercase hex chars
    total_steps: int
    total_model_calls: int
    total_tool_calls: int
    total_token_usage: TokenUsage
    total_cost: CostBreakdown
    status: str            # "ok"|"error"|"timeout"|"max_steps_exceeded"
    start_time_unix_nano: int
    end_time_unix_nano: int
    duration_ms: float
    termination_reason: str | None = None

    _VALID_STATUSES = frozenset({"ok", "error", "timeout", "max_steps_exceeded"})

    def __post_init__(self) -> None:
        if not isinstance(self.agent_run_id, str) or not self.agent_run_id:
            raise ValueError("AgentRunPayload.agent_run_id must be a non-empty string")
        if not isinstance(self.agent_name, str) or not self.agent_name:
            raise ValueError("AgentRunPayload.agent_name must be a non-empty string")
        if not _TRACE_ID_RE.match(self.trace_id):
            raise ValueError("AgentRunPayload.trace_id must be 32 lowercase hex chars")
        if not _SPAN_ID_RE.match(self.root_span_id):
            raise ValueError("AgentRunPayload.root_span_id must be 16 lowercase hex chars")
        for name in ("total_steps", "total_model_calls", "total_tool_calls"):
            v = getattr(self, name)
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"AgentRunPayload.{name} must be a non-negative int")
        status_val = self.status.value if isinstance(self.status, Enum) else self.status
        if status_val not in self._VALID_STATUSES:
            raise ValueError(f"AgentRunPayload.status must be one of {sorted(self._VALID_STATUSES)}")  # noqa: E501
        if self.start_time_unix_nano < 0:
            raise ValueError("AgentRunPayload.start_time_unix_nano must be non-negative")
        if self.end_time_unix_nano < self.start_time_unix_nano:
            raise ValueError("AgentRunPayload.end_time_unix_nano must be >= start_time_unix_nano")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the payload to a plain ``dict``."""
        st = self.status.value if isinstance(self.status, Enum) else self.status
        d: dict[str, Any] = {
            "agent_run_id": self.agent_run_id,
            "agent_name": self.agent_name,
            "trace_id": self.trace_id,
            "root_span_id": self.root_span_id,
            "total_steps": self.total_steps,
            "total_model_calls": self.total_model_calls,
            "total_tool_calls": self.total_tool_calls,
            "total_token_usage": self.total_token_usage.to_dict(),
            "total_cost": self.total_cost.to_dict(),
            "status": st,
            "start_time_unix_nano": self.start_time_unix_nano,
            "end_time_unix_nano": self.end_time_unix_nano,
            "duration_ms": self.duration_ms,
        }
        if self.termination_reason is not None:
            d["termination_reason"] = self.termination_reason
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRunPayload:
        """Deserialise from a plain ``dict``."""
        return cls(
            agent_run_id=data["agent_run_id"],
            agent_name=data["agent_name"],
            trace_id=data["trace_id"],
            root_span_id=data["root_span_id"],
            total_steps=int(data["total_steps"]),
            total_model_calls=int(data["total_model_calls"]),
            total_tool_calls=int(data["total_tool_calls"]),
            total_token_usage=TokenUsage.from_dict(data["total_token_usage"]),
            total_cost=CostBreakdown.from_dict(data["total_cost"]),
            status=data["status"],
            start_time_unix_nano=int(data["start_time_unix_nano"]),
            end_time_unix_nano=int(data["end_time_unix_nano"]),
            duration_ms=float(data["duration_ms"]),
            termination_reason=data.get("termination_reason"),
        )
