"""Supplementary coverage tests: validation errors + secondary payload classes.

Covers the remaining ``raise ValueError`` branches and untested payload classes
that ``test_sdk_coverage_boost.py`` left uncovered.
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# Secondary namespace payload classes
# ---------------------------------------------------------------------------
from agentobs.namespaces.diff import DiffComputedPayload, DiffRegressionFlaggedPayload
from agentobs.namespaces.eval_ import (
    EvalRegressionDetectedPayload,
    EvalScenarioStartedPayload,
    EvalScoreRecordedPayload,
)
from agentobs.namespaces.fence import (
    FenceMaxRetriesExceededPayload,
    FenceRetryTriggeredPayload,
    FenceValidatedPayload,
)
from agentobs.namespaces.prompt import PromptRenderedPayload, PromptVersionChangedPayload
from agentobs.namespaces.redact import RedactAppliedPayload, RedactPiiDetectedPayload
from agentobs.namespaces.template import (
    TemplateRegisteredPayload,
    TemplateValidationFailedPayload,
)

# ---------------------------------------------------------------------------
# trace.py validation paths
# ---------------------------------------------------------------------------
from agentobs.namespaces.trace import (
    AgentRunPayload,
    AgentStepPayload,
    CostBreakdown,
    DecisionPoint,
    GenAISystem,
    ModelInfo,
    PricingTier,
    ReasoningStep,
    SpanPayload,
    TokenUsage,
    ToolCall,
)

# Shared test fixtures
_SPAN_ID = "a" * 16
_TRACE_ID = "b" * 32
_SHA256 = "c" * 64


def _now() -> int:
    return int(time.time_ns())


def _token_usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def _cost() -> CostBreakdown:
    return CostBreakdown(input_cost_usd=0.001, output_cost_usd=0.002, total_cost_usd=0.003)


def _model() -> ModelInfo:
    return ModelInfo(system=GenAISystem.OPENAI, name="gpt-4o")


# ===========================================================================
# TokenUsage validation
# ===========================================================================

@pytest.mark.unit
class TestTokenUsageValidation:
    def test_negative_optional_field_raises(self) -> None:
        with pytest.raises(ValueError, match="cached_tokens"):
            TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cached_tokens=-1,  # must be non-negative
            )

    def test_negative_reasoning_tokens_raises(self) -> None:
        with pytest.raises(ValueError, match="reasoning_tokens"):
            TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                reasoning_tokens=-5,
            )

    def test_negative_image_tokens_raises(self) -> None:
        with pytest.raises(ValueError, match="image_tokens"):
            TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                image_tokens=-2,
            )


# ===========================================================================
# CostBreakdown validation
# ===========================================================================

@pytest.mark.unit
class TestCostBreakdownValidation:
    def test_mismatched_total_raises(self) -> None:
        with pytest.raises(ValueError, match="total_cost_usd"):
            CostBreakdown(
                input_cost_usd=0.001,
                output_cost_usd=0.002,
                total_cost_usd=0.999,  # wrong total
            )


# ===========================================================================
# PricingTier validation
# ===========================================================================

@pytest.mark.unit
class TestPricingTierValidation:
    def test_invalid_date_format_raises(self) -> None:
        with pytest.raises(ValueError, match="effective_date"):
            PricingTier(
                system=GenAISystem.OPENAI,
                model="m",
                input_per_million_usd=1.0,
                output_per_million_usd=2.0,
                effective_date="not-a-date",
            )

    def test_negative_input_price_raises(self) -> None:
        with pytest.raises(ValueError, match="input_per_million_usd"):
            PricingTier(
                system=GenAISystem.OPENAI,
                model="m",
                input_per_million_usd=-1.0,
                output_per_million_usd=2.0,
                effective_date="2024-01-01",
            )


# ===========================================================================
# ToolCall validation
# ===========================================================================

@pytest.mark.unit
class TestToolCallValidation:
    def test_empty_tool_call_id_raises(self) -> None:
        with pytest.raises(ValueError, match="tool_call_id"):
            ToolCall(tool_call_id="", function_name="fn", status="success")

    def test_empty_function_name_raises(self) -> None:
        with pytest.raises(ValueError, match="function_name"):
            ToolCall(tool_call_id="c1", function_name="", status="success")

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            ToolCall(tool_call_id="c1", function_name="fn", status="running")


# ===========================================================================
# ReasoningStep validation
# ===========================================================================

@pytest.mark.unit
class TestReasoningStepValidation:
    def test_negative_step_index_raises(self) -> None:
        with pytest.raises(ValueError, match="step_index"):
            ReasoningStep(step_index=-1, reasoning_tokens=10)

    def test_negative_reasoning_tokens_raises(self) -> None:
        with pytest.raises(ValueError, match="reasoning_tokens"):
            ReasoningStep(step_index=0, reasoning_tokens=-5)

    def test_negative_duration_raises(self) -> None:
        with pytest.raises(ValueError, match="duration_ms"):
            ReasoningStep(step_index=0, reasoning_tokens=5, duration_ms=-1.0)


# ===========================================================================
# DecisionPoint validation
# ===========================================================================

@pytest.mark.unit
class TestDecisionPointValidation:
    def test_empty_decision_id_raises(self) -> None:
        with pytest.raises(ValueError, match="decision_id"):
            DecisionPoint(
                decision_id="",
                decision_type="tool_selection",
                options_considered=["a"],
                chosen_option="a",
            )

    def test_invalid_decision_type_raises(self) -> None:
        with pytest.raises(ValueError, match="decision_type"):
            DecisionPoint(
                decision_id="dp-1",
                decision_type="invalid_type",
                options_considered=["a"],
                chosen_option="a",
            )

    def test_empty_options_raises(self) -> None:
        with pytest.raises(ValueError, match="options_considered"):
            DecisionPoint(
                decision_id="dp-1",
                decision_type="tool_selection",
                options_considered=[],
                chosen_option="a",
            )

    def test_empty_chosen_option_raises(self) -> None:
        with pytest.raises(ValueError, match="chosen_option"):
            DecisionPoint(
                decision_id="dp-1",
                decision_type="tool_selection",
                options_considered=["a"],
                chosen_option="",
            )


# ===========================================================================
# SpanPayload validation
# ===========================================================================

@pytest.mark.unit
class TestSpanPayloadValidation:
    def test_invalid_parent_span_id_raises(self) -> None:
        t0 = _now()
        t1 = t0 + 1_000_000
        with pytest.raises(ValueError, match="parent_span_id"):
            SpanPayload(
                span_id=_SPAN_ID,
                trace_id=_TRACE_ID,
                span_name="test",
                operation="chat",
                span_kind="CLIENT",
                status="ok",
                start_time_unix_nano=t0,
                end_time_unix_nano=t1,
                duration_ms=1.0,
                parent_span_id="tooshort",  # must be 16 hex chars
            )

    def test_end_before_start_raises(self) -> None:
        t0 = _now()
        with pytest.raises(ValueError, match="end_time_unix_nano"):
            SpanPayload(
                span_id=_SPAN_ID,
                trace_id=_TRACE_ID,
                span_name="test",
                operation="chat",
                span_kind="CLIENT",
                status="ok",
                start_time_unix_nano=t0,
                end_time_unix_nano=t0 - 1,  # before start
                duration_ms=0.0,
            )


# ===========================================================================
# AgentStepPayload validation
# ===========================================================================

@pytest.mark.unit
class TestAgentStepPayloadValidation:
    def test_empty_agent_run_id_raises(self) -> None:
        t0 = _now()
        t1 = t0 + 1_000_000
        with pytest.raises(ValueError, match="agent_run_id"):
            AgentStepPayload(
                agent_run_id="",
                step_index=0,
                span_id=_SPAN_ID,
                trace_id=_TRACE_ID,
                operation="chat",
                tool_calls=[],
                reasoning_steps=[],
                decision_points=[],
                status="ok",
                start_time_unix_nano=t0,
                end_time_unix_nano=t1,
                duration_ms=1.0,
            )

    def test_invalid_parent_span_id_raises(self) -> None:
        t0 = _now()
        t1 = t0 + 1_000_000
        with pytest.raises(ValueError, match="parent_span_id"):
            AgentStepPayload(
                agent_run_id="run-1",
                step_index=0,
                span_id=_SPAN_ID,
                trace_id=_TRACE_ID,
                operation="chat",
                tool_calls=[],
                reasoning_steps=[],
                decision_points=[],
                status="ok",
                start_time_unix_nano=t0,
                end_time_unix_nano=t1,
                duration_ms=1.0,
                parent_span_id="bad",
            )

    def test_end_before_start_raises(self) -> None:
        t0 = _now()
        with pytest.raises(ValueError, match="end_time_unix_nano"):
            AgentStepPayload(
                agent_run_id="run-1",
                step_index=0,
                span_id=_SPAN_ID,
                trace_id=_TRACE_ID,
                operation="chat",
                tool_calls=[],
                reasoning_steps=[],
                decision_points=[],
                status="ok",
                start_time_unix_nano=t0,
                end_time_unix_nano=t0 - 1,
                duration_ms=0.0,
            )

    def test_invalid_status_raises(self) -> None:
        t0 = _now()
        t1 = t0 + 1_000_000
        with pytest.raises(ValueError, match="status"):
            AgentStepPayload(
                agent_run_id="run-1",
                step_index=0,
                span_id=_SPAN_ID,
                trace_id=_TRACE_ID,
                operation="chat",
                tool_calls=[],
                reasoning_steps=[],
                decision_points=[],
                status="running",  # invalid
                start_time_unix_nano=t0,
                end_time_unix_nano=t1,
                duration_ms=1.0,
            )


# ===========================================================================
# AgentRunPayload from_dict with termination_reason
# ===========================================================================

@pytest.mark.unit
class TestAgentRunPayloadRoundtrip:
    def test_from_dict_with_termination_reason(self) -> None:
        t0 = _now()
        t1 = t0 + 10_000_000
        run = AgentRunPayload(
            agent_run_id="run-1",
            agent_name="bot",
            trace_id=_TRACE_ID,
            root_span_id=_SPAN_ID,
            total_steps=1,
            total_model_calls=1,
            total_tool_calls=0,
            total_token_usage=_token_usage(),
            total_cost=_cost(),
            status="max_steps_exceeded",
            start_time_unix_nano=t0,
            end_time_unix_nano=t1,
            duration_ms=10.0,
            termination_reason="hit_max",
        )
        d = run.to_dict()
        run2 = AgentRunPayload.from_dict(d)
        assert run2.termination_reason == "hit_max"
        assert run2.status == "max_steps_exceeded"

    def test_invalid_agent_run_id_raises(self) -> None:
        t0 = _now()
        t1 = t0 + 1_000_000
        with pytest.raises(ValueError, match="agent_run_id"):
            AgentRunPayload(
                agent_run_id="",
                agent_name="bot",
                trace_id=_TRACE_ID,
                root_span_id=_SPAN_ID,
                total_steps=0,
                total_model_calls=0,
                total_tool_calls=0,
                total_token_usage=_token_usage(),
                total_cost=_cost(),
                status="ok",
                start_time_unix_nano=t0,
                end_time_unix_nano=t1,
                duration_ms=1.0,
            )

    def test_end_before_start_raises(self) -> None:
        t0 = _now()
        with pytest.raises(ValueError, match="end_time"):
            AgentRunPayload(
                agent_run_id="run-1",
                agent_name="bot",
                trace_id=_TRACE_ID,
                root_span_id=_SPAN_ID,
                total_steps=0,
                total_model_calls=0,
                total_tool_calls=0,
                total_token_usage=_token_usage(),
                total_cost=_cost(),
                status="ok",
                start_time_unix_nano=t0,
                end_time_unix_nano=t0 - 1,
                duration_ms=0.0,
            )


# ===========================================================================
# DiffRegressionFlaggedPayload — secondary class
# ===========================================================================

@pytest.mark.unit
class TestDiffRegressionFlagged:
    def test_basic_creation(self) -> None:
        p = DiffRegressionFlaggedPayload(
            ref_event_id="ev-1",
            target_event_id="ev-2",
            diff_type="prompt",
            similarity_score=0.6,
            threshold=0.8,
            severity="high",
        )
        d = p.to_dict()
        assert d["diff_type"] == "prompt"
        assert d["similarity_score"] == pytest.approx(0.6)
        assert d["severity"] == "high"

    def test_with_optional_fields(self) -> None:
        p = DiffRegressionFlaggedPayload(
            ref_event_id="ev-1",
            target_event_id="ev-2",
            diff_type="response",
            similarity_score=0.5,
            threshold=0.9,
            severity="critical",
            diff_event_id="diff-1",
            alert_target="team-alert",
        )
        d = p.to_dict()
        assert d["diff_event_id"] == "diff-1"
        assert d["alert_target"] == "team-alert"

    def test_from_dict_roundtrip(self) -> None:
        p = DiffRegressionFlaggedPayload(
            ref_event_id="ev-1",
            target_event_id="ev-2",
            diff_type="cost",
            similarity_score=0.3,
            threshold=0.7,
            severity="low",
            diff_event_id="d1",
        )
        p2 = DiffRegressionFlaggedPayload.from_dict(p.to_dict())
        assert p2.diff_event_id == "d1"

    def test_validation_empty_ref_event_id_raises(self) -> None:
        with pytest.raises(ValueError):
            DiffRegressionFlaggedPayload(
                ref_event_id="",
                target_event_id="ev-2",
                diff_type="prompt",
                similarity_score=0.5,
                threshold=0.8,
                severity="low",
            )

    def test_validation_empty_target_event_id_raises(self) -> None:
        with pytest.raises(ValueError):
            DiffRegressionFlaggedPayload(
                ref_event_id="ev-1",
                target_event_id="",
                diff_type="prompt",
                similarity_score=0.5,
                threshold=0.8,
                severity="low",
            )

    def test_validation_out_of_range_score_raises(self) -> None:
        with pytest.raises(ValueError, match="similarity_score"):
            DiffRegressionFlaggedPayload(
                ref_event_id="ev-1",
                target_event_id="ev-2",
                diff_type="prompt",
                similarity_score=1.5,  # > 1
                threshold=0.8,
                severity="low",
            )

    def test_validation_invalid_severity_raises(self) -> None:
        with pytest.raises(ValueError, match="severity"):
            DiffRegressionFlaggedPayload(
                ref_event_id="ev-1",
                target_event_id="ev-2",
                diff_type="prompt",
                similarity_score=0.5,
                threshold=0.8,
                severity="extreme",  # not valid
            )


# ===========================================================================
# DiffComputedPayload validation
# ===========================================================================

@pytest.mark.unit
class TestDiffComputedValidation:
    def test_empty_ref_event_id_raises(self) -> None:
        with pytest.raises(ValueError):
            DiffComputedPayload(
                ref_event_id="",
                target_event_id="ev-2",
                diff_type="prompt",
                similarity_score=0.5,
            )

    def test_empty_target_event_id_raises(self) -> None:
        with pytest.raises(ValueError):
            DiffComputedPayload(
                ref_event_id="ev-1",
                target_event_id="",
                diff_type="prompt",
                similarity_score=0.5,
            )

    def test_out_of_range_score_raises(self) -> None:
        with pytest.raises(ValueError, match="similarity_score"):
            DiffComputedPayload(
                ref_event_id="ev-1",
                target_event_id="ev-2",
                diff_type="prompt",
                similarity_score=2.0,
            )

    def test_invalid_diff_algorithm_raises(self) -> None:
        with pytest.raises(ValueError, match="diff_algorithm"):
            DiffComputedPayload(
                ref_event_id="ev-1",
                target_event_id="ev-2",
                diff_type="prompt",
                similarity_score=0.5,
                diff_algorithm="bad_algo",
            )


# ===========================================================================
# FenceRetryTriggeredPayload validation
# ===========================================================================

@pytest.mark.unit
class TestFenceRetryValidation:
    def test_invalid_attempt_raises(self) -> None:
        with pytest.raises(ValueError, match="attempt"):
            FenceRetryTriggeredPayload(
                fence_id="f1",
                schema_name="S",
                attempt=0,  # must be >= 1
                max_attempts=3,
                violation_summary="bad",
            )

    def test_invalid_max_attempts_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            FenceRetryTriggeredPayload(
                fence_id="f1",
                schema_name="S",
                attempt=1,
                max_attempts=0,  # must be >= 1
                violation_summary="bad",
            )

    def test_invalid_output_type_raises(self) -> None:
        with pytest.raises(ValueError, match="output_type"):
            FenceRetryTriggeredPayload(
                fence_id="f1",
                schema_name="S",
                attempt=1,
                max_attempts=3,
                violation_summary="bad",
                output_type="invalid",
            )


# ===========================================================================
# FenceMaxRetriesExceededPayload validation
# ===========================================================================

@pytest.mark.unit
class TestFenceMaxRetriesValidation:
    def test_invalid_attempts_made_raises(self) -> None:
        with pytest.raises(ValueError, match="attempts_made"):
            FenceMaxRetriesExceededPayload(
                fence_id="f1",
                schema_name="S",
                attempts_made=0,  # must be >= 1
                final_violation_summary="still wrong",
            )

    def test_invalid_output_type_raises(self) -> None:
        with pytest.raises(ValueError, match="output_type"):
            FenceMaxRetriesExceededPayload(
                fence_id="f1",
                schema_name="S",
                attempts_made=3,
                final_violation_summary="still wrong",
                output_type="bad_type",
            )


# ===========================================================================
# EvalScoreRecordedPayload validation
# ===========================================================================

@pytest.mark.unit
class TestEvalScoreValidation:
    def test_empty_metric_name_raises(self) -> None:
        with pytest.raises(ValueError, match="metric_name"):
            EvalScoreRecordedPayload(
                evaluator="auto",
                metric_name="",
                score=0.9,
            )


# ===========================================================================
# EvalRegressionDetectedPayload validation
# ===========================================================================

@pytest.mark.unit
class TestEvalRegressionValidation:
    def test_invalid_severity_raises(self) -> None:
        with pytest.raises(ValueError, match="severity"):
            EvalRegressionDetectedPayload(
                metric_name="f1",
                baseline_score=0.9,
                current_score=0.7,
                delta=-0.2,
                regression_pct=22.2,
                severity="extreme",  # invalid
            )


# ===========================================================================
# EvalScenarioStartedPayload validation
# ===========================================================================

@pytest.mark.unit
class TestEvalScenarioStartedValidation:
    def test_empty_evaluator_raises(self) -> None:
        with pytest.raises(ValueError, match="evaluator"):
            EvalScenarioStartedPayload(
                scenario_id="sc-1",
                scenario_name="Test",
                evaluator="",
            )


# ===========================================================================
# PromptVersionChangedPayload — secondary class
# ===========================================================================

@pytest.mark.unit
class TestPromptVersionChanged:
    def test_basic_creation(self) -> None:
        p = PromptVersionChangedPayload(
            template_id="t1",
            previous_version="1.0",
            new_version="2.0",
            change_reason="feature update",
        )
        d = p.to_dict()
        assert d["new_version"] == "2.0"
        assert "changed_by" not in d

    def test_with_all_optionals(self) -> None:
        p = PromptVersionChangedPayload(
            template_id="t1",
            previous_version="1.0",
            new_version="2.0",
            change_reason="a/b test",
            changed_by="alice",
            previous_hash=_SHA256,
            new_hash="d" * 64,
        )
        d = p.to_dict()
        assert d["changed_by"] == "alice"
        assert "previous_hash" in d
        assert "new_hash" in d

    def test_from_dict_roundtrip(self) -> None:
        p = PromptVersionChangedPayload(
            template_id="t1",
            previous_version="1.0",
            new_version="2.0",
            change_reason="fix",
            changed_by="bob",
        )
        p2 = PromptVersionChangedPayload.from_dict(p.to_dict())
        assert p2.changed_by == "bob"
        assert p2.previous_hash is None


# ===========================================================================
# RedactAppliedPayload — secondary class
# ===========================================================================

@pytest.mark.unit
class TestRedactApplied:
    def test_basic_creation(self) -> None:
        p = RedactAppliedPayload(
            policy_min_sensitivity="HIGH",
            redacted_by="auto-redactor",
            redacted_count=3,
        )
        d = p.to_dict()
        assert d["redacted_count"] == 3

    def test_with_all_optionals(self) -> None:
        p = RedactAppliedPayload(
            policy_min_sensitivity="HIGH",
            redacted_by="auto-redactor",
            redacted_count=3,
            redacted_field_names=["user.ssn", "user.dob"],
            subject_event_id="ev-1",
            verified=True,
        )
        d = p.to_dict()
        assert d["redacted_field_names"] == ["user.ssn", "user.dob"]
        assert d["subject_event_id"] == "ev-1"
        assert d["verified"] is True

    def test_from_dict_roundtrip(self) -> None:
        p = RedactAppliedPayload(
            policy_min_sensitivity="PII",
            redacted_by="agent",
            redacted_count=1,
            redacted_field_names=["email"],
        )
        d = p.to_dict()
        p2 = RedactAppliedPayload.from_dict(d)
        assert p2.redacted_field_names == ["email"]

    def test_invalid_sensitivity_raises(self) -> None:
        with pytest.raises(ValueError, match="policy_min_sensitivity"):
            RedactAppliedPayload(
                policy_min_sensitivity="EXTREME",  # invalid
                redacted_by="agent",
                redacted_count=1,
            )


# ===========================================================================
# TemplateValidationFailedPayload — secondary class
# ===========================================================================

@pytest.mark.unit
class TestTemplateValidationFailed:
    def test_basic_creation(self) -> None:
        p = TemplateValidationFailedPayload(
            template_id="t1",
            version="1.0",
            failure_reason="missing variable: user_name",
        )
        d = p.to_dict()
        assert d["failure_reason"] == "missing variable: user_name"
        assert "failure_type" not in d

    def test_with_failure_type(self) -> None:
        p = TemplateValidationFailedPayload(
            template_id="t1",
            version="1.0",
            failure_reason="variable name is not defined",
            failure_type="missing_variable",
        )
        d = p.to_dict()
        assert d["failure_type"] == "missing_variable"

    def test_from_dict_roundtrip(self) -> None:
        p = TemplateValidationFailedPayload(
            template_id="t1",
            version="2.0",
            failure_reason="hash mismatch",
            failure_type="hash_mismatch",
        )
        p2 = TemplateValidationFailedPayload.from_dict(p.to_dict())
        assert p2.failure_type == "hash_mismatch"

    def test_invalid_failure_type_raises(self) -> None:
        with pytest.raises(ValueError, match="failure_type"):
            TemplateValidationFailedPayload(
                template_id="t1",
                version="1.0",
                failure_reason="something",
                failure_type="bad_type",
            )

    def test_empty_template_id_raises(self) -> None:
        with pytest.raises(ValueError, match="template_id"):
            TemplateValidationFailedPayload(
                template_id="",
                version="1.0",
                failure_reason="error",
            )


# ===========================================================================
# PromptRenderedPayload and -TemplateLoadedPayload validation
# ===========================================================================

@pytest.mark.unit
class TestPromptValidation:
    def test_empty_template_id_raises(self) -> None:
        with pytest.raises(ValueError, match="template_id"):
            PromptRenderedPayload(
                template_id="",
                version="1.0",
                rendered_hash=_SHA256,
            )

    def test_invalid_rendered_hash_raises(self) -> None:
        with pytest.raises(ValueError, match="rendered_hash"):
            PromptRenderedPayload(
                template_id="t1",
                version="1.0",
                rendered_hash="tooshort",
            )

    def test_invalid_source_raises_for_loaded(self) -> None:
        from agentobs.namespaces.prompt import PromptTemplateLoadedPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="source"):
            PromptTemplateLoadedPayload(
                template_id="t1",
                version="1.0",
                source="invalid_source",
            )


# ===========================================================================
# RedactPiiDetectedPayload validation
# ===========================================================================

@pytest.mark.unit
class TestRedactPiiValidation:
    def test_empty_detected_categories_raises(self) -> None:
        with pytest.raises(ValueError, match="detected_categories"):
            RedactPiiDetectedPayload(
                detected_categories=[],
                field_names=["field"],
                sensitivity_level="HIGH",
            )

    def test_empty_field_names_raises(self) -> None:
        with pytest.raises(ValueError, match="field_names"):
            RedactPiiDetectedPayload(
                detected_categories=["email"],
                field_names=[],
                sensitivity_level="HIGH",
            )


# ===========================================================================
# TemplateRegisteredPayload and VariableBound validation
# ===========================================================================

@pytest.mark.unit
class TestTemplateValidation:
    def test_empty_template_hash_raises(self) -> None:
        with pytest.raises(ValueError, match="template_hash"):
            TemplateRegisteredPayload(
                template_id="t1",
                version="1.0",
                template_hash="tooshort",
            )

    def test_invalid_value_type_raises(self) -> None:
        from agentobs.namespaces.template import TemplateVariableBoundPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="value_type"):
            TemplateVariableBoundPayload(
                template_id="t1",
                version="1.0",
                variable_name="x",
                value_type="invalid_type",
            )

    def test_invalid_value_hash_raises(self) -> None:
        from agentobs.namespaces.template import TemplateVariableBoundPayload  # noqa: PLC0415
        with pytest.raises(ValueError, match="value_hash"):
            TemplateVariableBoundPayload(
                template_id="t1",
                version="1.0",
                variable_name="x",
                value_hash="bad",
            )


# ===========================================================================
# FenceValidatedPayload validation
# ===========================================================================

@pytest.mark.unit
class TestFenceValidatedValidation:
    def test_invalid_output_type_raises(self) -> None:
        with pytest.raises(ValueError, match="output_type"):
            FenceValidatedPayload(
                fence_id="f1",
                schema_name="S",
                attempt=1,
                output_type="bad_type",
            )

    def test_invalid_attempt_raises(self) -> None:
        with pytest.raises(ValueError, match="attempt"):
            FenceValidatedPayload(
                fence_id="f1",
                schema_name="S",
                attempt=0,
            )
