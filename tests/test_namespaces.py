"""Tests for all v2.0 namespace payload dataclasses (RFC-0001 §8-§15).

Coverage targets
----------------
* Construction with required fields only.
* Construction with all optional fields.
* ``to_dict()`` / ``from_dict()`` round-trip.
* Validation errors for bad/missing required fields.
* Default field values (empty lists, None).
* Frozen / immutability where applicable.
"""

from __future__ import annotations

import pytest

# ── audit ─────────────────────────────────────────────────────────────────────
from agentobs.namespaces.audit import (
    AuditChainTamperedPayload,
    AuditChainVerifiedPayload,
    AuditKeyRotatedPayload,
)

# ── cache ─────────────────────────────────────────────────────────────────────
from agentobs.namespaces.cache import (
    CacheEvictedPayload,
    CacheHitPayload,
    CacheMissPayload,
    CacheWrittenPayload,
)

# ── cost ──────────────────────────────────────────────────────────────────────
from agentobs.namespaces.cost import (
    CostAttributedPayload,
    CostSessionRecordedPayload,
    CostTokenRecordedPayload,
)

# ── diff ──────────────────────────────────────────────────────────────────────
from agentobs.namespaces.diff import DiffComputedPayload, DiffRegressionFlaggedPayload

# ── eval_ ─────────────────────────────────────────────────────────────────────
from agentobs.namespaces.eval_ import (
    EvalRegressionDetectedPayload,
    EvalScenarioCompletedPayload,
    EvalScenarioStartedPayload,
    EvalScoreRecordedPayload,
)

# ── fence ─────────────────────────────────────────────────────────────────────
from agentobs.namespaces.fence import (
    FenceMaxRetriesExceededPayload,
    FenceRetryTriggeredPayload,
    FenceValidatedPayload,
)

# ── guard ─────────────────────────────────────────────────────────────────────
from agentobs.namespaces.guard import GuardPayload

# ── prompt ────────────────────────────────────────────────────────────────────
from agentobs.namespaces.prompt import (
    PromptRenderedPayload,
    PromptTemplateLoadedPayload,
    PromptVersionChangedPayload,
)

# ── redact ────────────────────────────────────────────────────────────────────
from agentobs.namespaces.redact import (
    RedactAppliedPayload,
    RedactPhiDetectedPayload,
    RedactPiiDetectedPayload,
)

# ── template ──────────────────────────────────────────────────────────────────
from agentobs.namespaces.template import (
    TemplateRegisteredPayload,
    TemplateValidationFailedPayload,
    TemplateVariableBoundPayload,
)

# ── trace ─────────────────────────────────────────────────────────────────────
from agentobs.namespaces.trace import (
    AgentRunPayload,
    AgentStepPayload,
    CostBreakdown,
    GenAIOperationName,
    GenAISystem,
    ModelInfo,
    SpanKind,
    SpanPayload,
    TokenUsage,
    ToolCall,
)

# ===========================================================================
# Constants / helpers
# ===========================================================================

SPAN_ID = "a" * 16
TRACE_ID = "b" * 32
TS_START = 1_700_000_000_000_000_000
TS_END   = 1_700_000_001_000_000_000


def _token_usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30)


def _model_info() -> ModelInfo:
    return ModelInfo(system="openai", name="gpt-4o")


def _cost_breakdown() -> CostBreakdown:
    return CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.002, total_cost_usd=0.003)


def _span_payload(**kw) -> SpanPayload:
    defaults = {
        "span_id": SPAN_ID,
        "trace_id": TRACE_ID,
        "span_name": "test_span",
        "operation": GenAIOperationName.CHAT,
        "span_kind": SpanKind.CLIENT,
        "status": "ok",
        "start_time_unix_nano": TS_START,
        "end_time_unix_nano": TS_END,
        "duration_ms": 1000.0,
    }
    defaults.update(kw)
    return SpanPayload(**defaults)


# ===========================================================================
# trace — primitive helpers
# ===========================================================================


class TestTokenUsage:
    def test_required_fields(self) -> None:
        tu = TokenUsage(input_tokens=5, output_tokens=10, total_tokens=15)
        assert tu.input_tokens == 5
        assert tu.cached_tokens is None

    def test_round_trip(self) -> None:
        tu = TokenUsage(input_tokens=5, output_tokens=10, total_tokens=15, cached_tokens=2)
        assert TokenUsage.from_dict(tu.to_dict()) == tu

    def test_to_dict_excludes_none_optionals(self) -> None:
        d = TokenUsage(input_tokens=5, output_tokens=10, total_tokens=15).to_dict()
        assert "cached_tokens" not in d

    def test_invalid_input_tokens(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            TokenUsage(input_tokens=-1, output_tokens=10, total_tokens=9)


class TestModelInfo:
    def test_required_fields(self) -> None:
        mi = ModelInfo(system="openai", name="gpt-4o")
        assert mi.system == GenAISystem.OPENAI
        assert mi.name == "gpt-4o"

    def test_unknown_system_stored_as_string(self) -> None:
        mi = ModelInfo(system="custom_provider", name="custom-model")
        assert mi.system == "custom_provider"

    def test_round_trip(self) -> None:
        mi = ModelInfo(system="openai", name="gpt-4o", version="2024-11")
        assert ModelInfo.from_dict(mi.to_dict()) == mi

    def test_invalid_empty_name(self) -> None:
        with pytest.raises(ValueError):
            ModelInfo(system="openai", name="")


class TestCostBreakdown:
    def test_required_fields(self) -> None:
        cb = CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.002, total_cost_usd=0.003)
        assert cb.currency == "USD"

    def test_round_trip(self) -> None:
        cb = CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.002, total_cost_usd=0.003)
        assert CostBreakdown.from_dict(cb.to_dict()) == cb

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValueError):
            CostBreakdown(input_cost_usd=-0.001, output_cost_usd=0.002, total_cost_usd=0.001)


class TestToolCall:
    def test_required_fields(self) -> None:
        tc = ToolCall(tool_call_id="tc_001", function_name="search", status="success")
        assert tc.arguments_hash is None
        assert tc.duration_ms is None

    def test_round_trip(self) -> None:
        tc = ToolCall(tool_call_id="tc_001", function_name="search", status="success", duration_ms=42.5)  # noqa: E501
        assert ToolCall.from_dict(tc.to_dict()) == tc

    def test_invalid_status(self) -> None:
        with pytest.raises(ValueError):
            ToolCall(tool_call_id="tc_001", function_name="search", status="completed")


# ===========================================================================
# trace — SpanPayload
# ===========================================================================


class TestSpanPayload:
    def test_required_fields_only(self) -> None:
        sp = _span_payload()
        assert sp.span_name == "test_span"
        assert sp.status == "ok"
        assert sp.tool_calls == []
        assert sp.model is None

    def test_with_optional_fields(self) -> None:
        sp = _span_payload(
            model=_model_info(),
            token_usage=_token_usage(),
            cost=_cost_breakdown(),
            error="timeout",
        )
        assert sp.model.name == "gpt-4o"
        assert sp.token_usage.total_tokens == 30

    def test_round_trip(self) -> None:
        sp = _span_payload(model=_model_info(), token_usage=_token_usage())
        restored = SpanPayload.from_dict(sp.to_dict())
        assert restored.span_id == sp.span_id
        assert restored.span_name == sp.span_name
        assert restored.model.name == sp.model.name

    def test_invalid_span_id_length(self) -> None:
        with pytest.raises(ValueError):
            _span_payload(span_id="tooshort")

    def test_invalid_trace_id_length(self) -> None:
        with pytest.raises(ValueError):
            _span_payload(trace_id="tooshort")

    def test_invalid_status(self) -> None:
        with pytest.raises(ValueError):
            _span_payload(status="completed")

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValueError):
            _span_payload(start_time_unix_nano=TS_END, end_time_unix_nano=TS_START)

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValueError):
            _span_payload(duration_ms=-1.0)

    def test_to_dict_contains_required_keys(self) -> None:
        d = _span_payload().to_dict()
        for key in ("span_id", "trace_id", "span_name", "operation", "span_kind", "status"):
            assert key in d


# ===========================================================================
# trace — AgentStepPayload
# ===========================================================================


class TestAgentStepPayload:
    def _make(self, **kw) -> AgentStepPayload:
        defaults = {
            "agent_run_id": "run_001",
            "step_index": 0,
            "span_id": SPAN_ID,
            "trace_id": TRACE_ID,
            "operation": GenAIOperationName.CHAT,
            "tool_calls": [],
            "reasoning_steps": [],
            "decision_points": [],
            "status": "ok",
            "start_time_unix_nano": TS_START,
            "end_time_unix_nano": TS_END,
            "duration_ms": 500.0,
        }
        defaults.update(kw)
        return AgentStepPayload(**defaults)

    def test_required_fields(self) -> None:
        step = self._make()
        assert step.step_index == 0
        assert step.model is None

    def test_round_trip(self) -> None:
        step = self._make(model=_model_info(), token_usage=_token_usage())
        restored = AgentStepPayload.from_dict(step.to_dict())
        assert restored.agent_run_id == step.agent_run_id
        assert restored.model.name == step.model.name

    def test_negative_step_index_rejected(self) -> None:
        with pytest.raises(ValueError):
            self._make(step_index=-1)

    def test_empty_agent_run_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            self._make(agent_run_id="")

    def test_invalid_status(self) -> None:
        with pytest.raises(ValueError):
            self._make(status="done")


# ===========================================================================
# trace — AgentRunPayload
# ===========================================================================


class TestAgentRunPayload:
    def _make(self, **kw) -> AgentRunPayload:
        defaults = {
            "agent_run_id": "run_001",
            "agent_name": "customer_support_agent",
            "trace_id": TRACE_ID,
            "root_span_id": SPAN_ID,
            "total_steps": 3,
            "total_model_calls": 5,
            "total_tool_calls": 2,
            "total_token_usage": _token_usage(),
            "total_cost": _cost_breakdown(),
            "status": "ok",
            "start_time_unix_nano": TS_START,
            "end_time_unix_nano": TS_END,
            "duration_ms": 2000.0,
        }
        defaults.update(kw)
        return AgentRunPayload(**defaults)

    def test_required_fields(self) -> None:
        run = self._make()
        assert run.agent_name == "customer_support_agent"
        assert run.termination_reason is None

    def test_round_trip(self) -> None:
        run = self._make()
        restored = AgentRunPayload.from_dict(run.to_dict())
        assert restored.agent_run_id == run.agent_run_id
        assert restored.total_steps == run.total_steps

    def test_invalid_status(self) -> None:
        with pytest.raises(ValueError):
            self._make(status="done")

    def test_negative_steps_rejected(self) -> None:
        with pytest.raises(ValueError):
            self._make(total_steps=-1)

    def test_empty_agent_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            self._make(agent_name="")


# ===========================================================================
# cost
# ===========================================================================


class TestCostPayloads:
    def test_cost_token_recorded_required(self) -> None:
        p = CostTokenRecordedPayload(
            cost=_cost_breakdown(),
            token_usage=_token_usage(),
            model=_model_info(),
        )
        assert p.span_id is None

    def test_cost_token_recorded_round_trip(self) -> None:
        p = CostTokenRecordedPayload(
            cost=_cost_breakdown(),
            token_usage=_token_usage(),
            model=_model_info(),
            span_id="abc123def456abcd",
        )
        restored = CostTokenRecordedPayload.from_dict(p.to_dict())
        assert restored.span_id == p.span_id
        assert restored.model.name == p.model.name

    def test_cost_token_recorded_rejects_bad_cost(self) -> None:
        with pytest.raises(TypeError):
            CostTokenRecordedPayload(cost="not-a-breakdown", token_usage=_token_usage(), model=_model_info())  # type: ignore[arg-type]  # noqa: E501

    def test_cost_session_recorded_required(self) -> None:
        p = CostSessionRecordedPayload(
            total_cost=_cost_breakdown(),
            total_token_usage=_token_usage(),
            call_count=5,
        )
        assert p.call_count == 5
        assert p.session_duration_ms is None

    def test_cost_session_recorded_round_trip(self) -> None:
        p = CostSessionRecordedPayload(
            total_cost=_cost_breakdown(),
            total_token_usage=_token_usage(),
            call_count=5,
        )
        restored = CostSessionRecordedPayload.from_dict(p.to_dict())
        assert restored.call_count == 5

    def test_cost_attributed_required(self) -> None:
        p = CostAttributedPayload(
            cost=_cost_breakdown(),
            attribution_target="user_abc",
            attribution_type="direct",
        )
        assert p.attribution_type == "direct"

    def test_cost_attributed_round_trip(self) -> None:
        p = CostAttributedPayload(
            cost=_cost_breakdown(),
            attribution_target="user_abc",
            attribution_type="proportional",
        )
        restored = CostAttributedPayload.from_dict(p.to_dict())
        assert restored.attribution_target == "user_abc"

    def test_cost_attributed_invalid_type(self) -> None:
        with pytest.raises(ValueError):
            CostAttributedPayload(cost=_cost_breakdown(), attribution_target="x", attribution_type="unknown")  # noqa: E501


# ===========================================================================
# diff
# ===========================================================================


class TestDiffPayloads:
    def test_diff_computed_required(self) -> None:
        p = DiffComputedPayload(
            ref_event_id="a" * 26,
            target_event_id="b" * 26,
            diff_type="response",
            similarity_score=0.95,
        )
        assert p.similarity_score == pytest.approx(0.95)
        assert p.added_tokens is None

    def test_diff_computed_round_trip(self) -> None:
        p = DiffComputedPayload(
            ref_event_id="a" * 26,
            target_event_id="b" * 26,
            diff_type="token_usage",
            similarity_score=0.80,
        )
        restored = DiffComputedPayload.from_dict(p.to_dict())
        assert restored.diff_type == "token_usage"
        assert restored.similarity_score == pytest.approx(0.80)

    def test_diff_computed_similarity_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            DiffComputedPayload(
                ref_event_id="a" * 26, target_event_id="b" * 26,
                diff_type="text", similarity_score=1.5,
            )

    def test_diff_regression_flagged_required(self) -> None:
        p = DiffRegressionFlaggedPayload(
            ref_event_id="a" * 26,
            target_event_id="b" * 26,
            diff_type="response",
            similarity_score=0.3,
            threshold=0.9,
            severity="high",
        )
        assert p.severity == "high"

    def test_diff_regression_flagged_round_trip(self) -> None:
        p = DiffRegressionFlaggedPayload(
            ref_event_id="a" * 26,
            target_event_id="b" * 26,
            diff_type="prompt",
            similarity_score=0.3,
            threshold=0.9,
            severity="medium",
        )
        restored = DiffRegressionFlaggedPayload.from_dict(p.to_dict())
        assert restored.threshold == pytest.approx(0.9)

    def test_diff_regression_invalid_severity(self) -> None:
        with pytest.raises(ValueError):
            DiffRegressionFlaggedPayload(
                ref_event_id="a" * 26, target_event_id="b" * 26,
                diff_type="response", similarity_score=0.3,
                threshold=0.9, severity="extreme",
            )


# ===========================================================================
# eval_
# ===========================================================================


class TestEvalPayloads:
    def test_score_recorded_required(self) -> None:
        p = EvalScoreRecordedPayload(evaluator="gpt-4o-judge", metric_name="accuracy", score=0.92)
        assert p.passed is None
        assert p.rationale is None

    def test_score_recorded_round_trip(self) -> None:
        p = EvalScoreRecordedPayload(
            evaluator="gpt-4o-judge", metric_name="accuracy", score=0.92,
            passed=True, threshold=0.85,
        )
        restored = EvalScoreRecordedPayload.from_dict(p.to_dict())
        assert restored.passed is True
        assert restored.threshold == pytest.approx(0.85)

    def test_score_recorded_empty_evaluator_rejected(self) -> None:
        with pytest.raises(ValueError):
            EvalScoreRecordedPayload(evaluator="", metric_name="accuracy", score=0.5)

    def test_regression_detected_required(self) -> None:
        p = EvalRegressionDetectedPayload(
            metric_name="accuracy",
            baseline_score=0.90,
            current_score=0.70,
            delta=-0.20,
            regression_pct=22.2,
        )
        assert p.severity is None

    def test_regression_detected_round_trip(self) -> None:
        p = EvalRegressionDetectedPayload(
            metric_name="f1",
            baseline_score=0.88,
            current_score=0.75,
            delta=-0.13,
            regression_pct=14.8,
            severity="high",
        )
        restored = EvalRegressionDetectedPayload.from_dict(p.to_dict())
        assert restored.severity == "high"

    def test_scenario_started_required(self) -> None:
        p = EvalScenarioStartedPayload(scenario_id="sc_01", scenario_name="rag_accuracy", evaluator="harness")  # noqa: E501
        assert p.dataset_id is None

    def test_scenario_started_round_trip(self) -> None:
        p = EvalScenarioStartedPayload(scenario_id="sc_01", scenario_name="rag", evaluator="harness")  # noqa: E501
        restored = EvalScenarioStartedPayload.from_dict(p.to_dict())
        assert restored.scenario_name == "rag"

    def test_scenario_completed_required(self) -> None:
        p = EvalScenarioCompletedPayload(scenario_id="sc_01", status="passed", duration_ms=1234.5)
        assert p.errors is None

    def test_scenario_completed_invalid_status(self) -> None:
        with pytest.raises(ValueError):
            EvalScenarioCompletedPayload(scenario_id="sc_01", status="unknown", duration_ms=1.0)

    def test_scenario_completed_round_trip(self) -> None:
        p = EvalScenarioCompletedPayload(scenario_id="sc_01", status="failed", duration_ms=500.0)
        restored = EvalScenarioCompletedPayload.from_dict(p.to_dict())
        assert restored.status == "failed"


# ===========================================================================
# fence
# ===========================================================================


class TestFencePayloads:
    def test_validated_required(self) -> None:
        p = FenceValidatedPayload(fence_id="fence_01", schema_name="CustomerOrder", attempt=1)
        assert p.output_type is None

    def test_validated_round_trip(self) -> None:
        p = FenceValidatedPayload(
            fence_id="fence_01", schema_name="CustomerOrder", attempt=2,
            output_type="json_schema", validation_duration_ms=3.5,
        )
        restored = FenceValidatedPayload.from_dict(p.to_dict())
        assert restored.output_type == "json_schema"
        assert restored.attempt == 2

    def test_validated_attempt_zero_rejected(self) -> None:
        with pytest.raises(ValueError):
            FenceValidatedPayload(fence_id="f", schema_name="s", attempt=0)

    def test_validated_invalid_output_type(self) -> None:
        with pytest.raises(ValueError):
            FenceValidatedPayload(fence_id="f", schema_name="s", attempt=1, output_type="binary")

    def test_retry_triggered_required(self) -> None:
        p = FenceRetryTriggeredPayload(
            fence_id="fence_01", schema_name="CustomerOrder",
            attempt=1, max_attempts=3, violation_summary="missing field",
        )
        assert p.attempt == 1

    def test_retry_triggered_round_trip(self) -> None:
        p = FenceRetryTriggeredPayload(
            fence_id="fence_01", schema_name="Order",
            attempt=2, max_attempts=3, violation_summary="invalid type",
        )
        restored = FenceRetryTriggeredPayload.from_dict(p.to_dict())
        assert restored.violation_summary == "invalid type"
        assert restored.max_attempts == 3

    def test_max_retries_exceeded_required(self) -> None:
        p = FenceMaxRetriesExceededPayload(
            fence_id="fence_01", schema_name="Order",
            attempts_made=3, final_violation_summary="still missing",
        )
        assert p.attempts_made == 3

    def test_max_retries_exceeded_round_trip(self) -> None:
        p = FenceMaxRetriesExceededPayload(
            fence_id="f1", schema_name="S1",
            attempts_made=3, final_violation_summary="bad json",
        )
        restored = FenceMaxRetriesExceededPayload.from_dict(p.to_dict())
        assert restored.fence_id == "f1"


# ===========================================================================
# guard
# ===========================================================================


class TestGuardPayload:
    def test_required_fields(self) -> None:
        p = GuardPayload(classifier="pii_classifier", direction="input", action="blocked", score=0.95)  # noqa: E501
        assert p.categories == []
        assert p.latency_ms is None

    def test_round_trip(self) -> None:
        p = GuardPayload(
            classifier="toxicity", direction="output", action="flagged", score=0.71,
            categories=["hate_speech"], threshold=0.5,
        )
        restored = GuardPayload.from_dict(p.to_dict())
        assert restored.action == "flagged"
        assert restored.categories == ["hate_speech"]

    def test_invalid_direction(self) -> None:
        with pytest.raises(ValueError):
            GuardPayload(classifier="cls", direction="both", action="blocked", score=0.9)

    def test_invalid_action(self) -> None:
        with pytest.raises(ValueError):
            GuardPayload(classifier="cls", direction="input", action="denied", score=0.9)

    def test_empty_classifier_rejected(self) -> None:
        with pytest.raises(ValueError):
            GuardPayload(classifier="", direction="input", action="blocked", score=0.9)

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValueError):
            GuardPayload(classifier="cls", direction="input", action="passed", score=0.1, latency_ms=-1.0)  # noqa: E501


# ===========================================================================
# prompt
# ===========================================================================


class TestPromptPayloads:
    def test_rendered_required(self) -> None:
        p = PromptRenderedPayload(template_id="tmpl_01", version="v3", rendered_hash="a" * 64)
        assert p.span_id is None

    def test_rendered_round_trip(self) -> None:
        p = PromptRenderedPayload(
            template_id="tmpl_01", version="v3",
            rendered_hash="a" * 64, span_id="a" * 16,
        )
        restored = PromptRenderedPayload.from_dict(p.to_dict())
        assert restored.template_id == "tmpl_01"

    def test_rendered_invalid_hash_length(self) -> None:
        with pytest.raises(ValueError):
            PromptRenderedPayload(template_id="tmpl_01", version="v1", rendered_hash="tooshort")

    def test_template_loaded_required(self) -> None:
        p = PromptTemplateLoadedPayload(template_id="tmpl_02", version="v1", source="registry")
        assert p.cache_hit is None

    def test_template_loaded_round_trip(self) -> None:
        p = PromptTemplateLoadedPayload(
            template_id="tmpl_02", version="v1", source="file", cache_hit=True,
        )
        restored = PromptTemplateLoadedPayload.from_dict(p.to_dict())
        assert restored.cache_hit is True

    def test_template_loaded_invalid_source(self) -> None:
        with pytest.raises(ValueError):
            PromptTemplateLoadedPayload(template_id="t", version="v1", source="filesystem")

    def test_version_changed_required(self) -> None:
        p = PromptVersionChangedPayload(
            template_id="tmpl_03",
            previous_version="v1",
            new_version="v2",
            change_reason="Bug fix",
        )
        assert p.changed_by is None

    def test_version_changed_round_trip(self) -> None:
        p = PromptVersionChangedPayload(
            template_id="tmpl_03",
            previous_version="v1",
            new_version="v2",
            change_reason="Performance improvement",
            changed_by="alice@acme.com",
        )
        restored = PromptVersionChangedPayload.from_dict(p.to_dict())
        assert restored.changed_by == "alice@acme.com"

    def test_version_changed_empty_reason_rejected(self) -> None:
        with pytest.raises(ValueError):
            PromptVersionChangedPayload(
                template_id="t", previous_version="v1", new_version="v2", change_reason="",
            )


# ===========================================================================
# template
# ===========================================================================


class TestTemplatePayloads:
    def test_registered_required(self) -> None:
        p = TemplateRegisteredPayload(template_id="tmpl_01", version="v1", template_hash="c" * 64)
        assert p.registered_by is None

    def test_registered_round_trip(self) -> None:
        p = TemplateRegisteredPayload(
            template_id="tmpl_01", version="v1", template_hash="c" * 64,
            registered_by="alice",
        )
        restored = TemplateRegisteredPayload.from_dict(p.to_dict())
        assert restored.registered_by == "alice"

    def test_registered_invalid_hash(self) -> None:
        with pytest.raises(ValueError):
            TemplateRegisteredPayload(template_id="t", version="v1", template_hash="bad")

    def test_variable_bound_required(self) -> None:
        p = TemplateVariableBoundPayload(template_id="tmpl_01", version="v1", variable_name="user_name")  # noqa: E501
        assert p.value_hash is None

    def test_variable_bound_round_trip(self) -> None:
        p = TemplateVariableBoundPayload(
            template_id="tmpl_01", version="v1", variable_name="org_name", value_hash="d" * 64,
        )
        restored = TemplateVariableBoundPayload.from_dict(p.to_dict())
        assert restored.variable_name == "org_name"

    def test_validation_failed_required(self) -> None:
        p = TemplateValidationFailedPayload(
            template_id="tmpl_01", version="v1", failure_reason="missing variable"
        )
        assert p.failure_type is None

    def test_validation_failed_round_trip(self) -> None:
        p = TemplateValidationFailedPayload(
            template_id="tmpl_01", version="v1",
            failure_reason="missing vars", failure_type="missing_variable",
        )
        restored = TemplateValidationFailedPayload.from_dict(p.to_dict())
        assert restored.failure_type == "missing_variable"


# ===========================================================================
# cache
# ===========================================================================


class TestCachePayloads:
    def test_hit_required(self) -> None:
        p = CacheHitPayload(key_hash="a" * 64, namespace="llm_responses", similarity_score=0.98)
        assert p.ttl_remaining_seconds is None

    def test_hit_round_trip(self) -> None:
        p = CacheHitPayload(key_hash="a" * 64, namespace="llm_responses", similarity_score=0.98)
        restored = CacheHitPayload.from_dict(p.to_dict())
        assert restored.similarity_score == pytest.approx(0.98)

    def test_hit_similarity_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            CacheHitPayload(key_hash="a" * 64, namespace="ns", similarity_score=1.1)

    def test_miss_required(self) -> None:
        p = CacheMissPayload(key_hash="b" * 64, namespace="llm_responses")
        assert p.best_similarity_score is None

    def test_miss_round_trip(self) -> None:
        p = CacheMissPayload(key_hash="b" * 64, namespace="llm_responses", best_similarity_score=0.5)  # noqa: E501
        restored = CacheMissPayload.from_dict(p.to_dict())
        assert restored.best_similarity_score == pytest.approx(0.5)

    def test_evicted_required(self) -> None:
        p = CacheEvictedPayload(key_hash="c" * 64, namespace="llm_responses", eviction_reason="ttl_expired")  # noqa: E501
        assert p.entry_age_seconds is None

    def test_evicted_invalid_reason(self) -> None:
        with pytest.raises(ValueError):
            CacheEvictedPayload(key_hash="c" * 64, namespace="ns", eviction_reason="unknown_reason")

    def test_evicted_round_trip(self) -> None:
        p = CacheEvictedPayload(key_hash="c" * 64, namespace="ns", eviction_reason="capacity_exceeded")  # noqa: E501
        restored = CacheEvictedPayload.from_dict(p.to_dict())
        assert restored.eviction_reason == "capacity_exceeded"

    def test_written_required(self) -> None:
        p = CacheWrittenPayload(key_hash="d" * 64, namespace="llm_responses", ttl_seconds=3600)
        assert p.model is None

    def test_written_round_trip(self) -> None:
        p = CacheWrittenPayload(key_hash="d" * 64, namespace="ns", ttl_seconds=600)
        restored = CacheWrittenPayload.from_dict(p.to_dict())
        assert restored.ttl_seconds == 600


# ===========================================================================
# redact
# ===========================================================================


class TestRedactPayloads:
    def test_pii_detected_required(self) -> None:
        p = RedactPiiDetectedPayload(
            detected_categories=["email", "phone"],
            field_names=["user_message"],
            sensitivity_level="HIGH",
        )
        assert p.detector is None

    def test_pii_detected_round_trip(self) -> None:
        p = RedactPiiDetectedPayload(
            detected_categories=["ssn"],
            field_names=["input_text"],
            sensitivity_level="PII",
        )
        restored = RedactPiiDetectedPayload.from_dict(p.to_dict())
        assert restored.detected_categories == ["ssn"]

    def test_pii_detected_invalid_sensitivity(self) -> None:
        with pytest.raises(ValueError):
            RedactPiiDetectedPayload(
                detected_categories=["email"],
                field_names=["msg"],
                sensitivity_level="critical",
            )

    def test_phi_detected_required(self) -> None:
        p = RedactPhiDetectedPayload(
            detected_categories=["diagnosis"],
            field_names=["patient_notes"],
        )
        assert p.detector is None

    def test_phi_detected_round_trip(self) -> None:
        p = RedactPhiDetectedPayload(
            detected_categories=["medication"],
            field_names=["notes"],
        )
        restored = RedactPhiDetectedPayload.from_dict(p.to_dict())
        assert restored.field_names == ["notes"]

    def test_applied_required(self) -> None:
        p = RedactAppliedPayload(
            policy_min_sensitivity="MEDIUM",
            redacted_by="pii-shield-v2",
            redacted_count=3,
        )
        assert p.redacted_field_names == []

    def test_applied_round_trip(self) -> None:
        p = RedactAppliedPayload(
            policy_min_sensitivity="HIGH",
            redacted_by="shield",
            redacted_count=1,
            redacted_field_names=["email"],
        )
        restored = RedactAppliedPayload.from_dict(p.to_dict())
        assert restored.redacted_field_names == ["email"]


# ===========================================================================
# audit
# ===========================================================================


class TestAuditPayloads:
    def test_key_rotated_required(self) -> None:
        p = AuditKeyRotatedPayload(
            key_id="key_002",
            previous_key_id="key_001",
            rotated_at="2026-03-01T12:00:00Z",
            rotated_by="infra-bot",
        )
        assert p.rotation_reason is None

    def test_key_rotated_round_trip(self) -> None:
        p = AuditKeyRotatedPayload(
            key_id="key_002",
            previous_key_id="key_001",
            rotated_at="2026-03-01T12:00:00Z",
            rotated_by="infra-bot",
            rotation_reason="scheduled",
        )
        restored = AuditKeyRotatedPayload.from_dict(p.to_dict())
        assert restored.rotation_reason == "scheduled"

    def test_chain_verified_required(self) -> None:
        p = AuditChainVerifiedPayload(
            verified_from_event_id="a" * 26,
            verified_to_event_id="b" * 26,
            event_count=100,
            verified_at="2026-03-01T12:00:00Z",
            verified_by="audit-service",
        )
        assert p.event_count == 100

    def test_chain_verified_round_trip(self) -> None:
        p = AuditChainVerifiedPayload(
            verified_from_event_id="a" * 26,
            verified_to_event_id="b" * 26,
            event_count=50,
            verified_at="2026-03-01T12:00:00Z",
            verified_by="audit-bot",
        )
        restored = AuditChainVerifiedPayload.from_dict(p.to_dict())
        assert restored.event_count == 50

    def test_chain_tampered_required(self) -> None:
        p = AuditChainTamperedPayload(
            first_tampered_event_id="c" * 26,
            tampered_count=3,
            detected_at="2026-03-01T12:00:00Z",
            detected_by="integrity-guard",
        )
        assert p.gap_count is None

    def test_chain_tampered_round_trip(self) -> None:
        p = AuditChainTamperedPayload(
            first_tampered_event_id="c" * 26,
            tampered_count=1,
            detected_at="2026-03-01T12:00:00Z",
            detected_by="guard",
            severity="high",
        )
        restored = AuditChainTamperedPayload.from_dict(p.to_dict())
        assert restored.severity == "high"
